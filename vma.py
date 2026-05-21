#!/usr/bin/env python3
import os
import sys
import hashlib
import struct
import argparse


OUTPUT_FORMAT_ORIGINAL = 'original'
OUTPUT_FORMAT_RAW = 'raw'
ZERO_BLOCK_SIZE = 4096


def enable_sparse_file(fo):
    if os.name != 'nt':
        return False

    try:
        import ctypes
        import msvcrt
    except ImportError:
        return False

    fsctl_set_sparse = 0x000900C4
    bytes_returned = ctypes.c_ulong(0)
    handle = msvcrt.get_osfhandle(fo.fileno())
    return bool(ctypes.windll.kernel32.DeviceIoControl(
        handle,
        fsctl_set_sparse,
        None,
        0,
        None,
        0,
        ctypes.byref(bytes_returned),
        None,
    ))


def skip_zeroes(fo, size):
    if size > 0:
        fo.seek(size, os.SEEK_CUR)


def device_output_name(device_name, output_format=OUTPUT_FORMAT_ORIGINAL):
    if output_format == OUTPUT_FORMAT_RAW and not device_name.lower().endswith('.raw'):
        return f'{device_name}.raw'
    return device_name


class VmaHeader():
    def __init__(self, fo, skip_hash):
        # 0 -  3:   magic
        #     VMA magic string ("VMA\x00")
        magic = fo.read(4)
        if magic != b'VMA\0':
            raise ValueError('不是有效的 VMA 文件；如果源文件是 .vma.zst，请先解压或使用桌面界面自动解压。')

        # 4 -  7:   version
        #     Version number (valid value is 1)
        version = int.from_bytes(fo.read(4), 'big')
        if version != 1:
            raise ValueError(f'不支持的 VMA 版本：{version}')

        # 8 - 23:   uuid
        #     Unique ID, Same uuid is used to mark extents.
        self.uuid = fo.read(16)

        # 24 - 31:   ctime
        #     Backup time stamp (seconds since epoch)
        self.ctime = int.from_bytes(fo.read(8), 'big')

        # 32 - 47:   md5sum
        #     Header checksum (from byte 0 to header_size). This field
        #     is filled with zero to generate the checksum.
        self.md5sum = fo.read(16)

        # 48 - 51:   blob_buffer_offset
        #     Start of blob buffer (multiple of 512)
        self.blob_buffer_offset = int.from_bytes(fo.read(4), 'big')

        # 52 - 55:   blob_buffer_size
        #     Size of blob buffer (multiple of 512)
        self.blob_buffer_size = int.from_bytes(fo.read(4), 'big')

        # 56 - 59:   header_size
        #     Overall size of this header (multiple of 512)
        self.header_size = int.from_bytes(fo.read(4), 'big')

        # 60 - 2043: reserved
        fo.seek(1984, os.SEEK_CUR)

        # 2044 - 3067: uint32_t config_names[256]
        #     Offsets into blob_buffer table
        self.config_names = []
        for i in range(256):
            self.config_names.append(int.from_bytes(fo.read(4), 'big'))

        # 3068 - 4091: uint32_t config_data[256]
        #     Offsets into blob_buffer table
        self.config_data = []
        for i in range(256):
            self.config_data.append(int.from_bytes(fo.read(4), 'big'))

        # 4092 - 4095: reserved
        fo.seek(4, os.SEEK_CUR)

        # 4096 - 12287: VmaDeviceInfoHeader dev_info[256]
        #     The offset in this table is used as 'dev_id' inside
        #     the data streams.
        self.dev_info = []
        for i in range(256):
            self.dev_info.append(VmaDeviceInfoHeader(fo, self))

        # 12288 - header_size: Blob buffer

        # the blob buffer layout is very odd. there appears to be an additional
        # byte of padding at the beginning
        fo.seek(1, os.SEEK_CUR)
        # since byte-wise offsets are used to address the blob buffer, the
        # blob metadata is stored in a hashmap, with the offsets as the keys
        self.blob_buffer = {}
        blob_buffer_current_offset = 1
        while(fo.tell() < self.blob_buffer_offset + self.blob_buffer_size):
            self.blob_buffer[blob_buffer_current_offset] = Blob(fo)
            blob_buffer_current_offset = fo.tell() - self.blob_buffer_offset

        # make sure the file object points at the end of the vma header
        fo.seek(self.header_size, os.SEEK_SET)

        # reread the header and generate a md5 checksum of the data
        if skip_hash:
            self.generated_md5sum = None
        else:
            self.generated_md5sum = self.__gen_md5sum(fo)


    def __gen_md5sum(self, fo):
        p = fo.tell()
        fo.seek(0, os.SEEK_SET)
        h = hashlib.md5()

        data = fo.read(self.header_size)
        data = data[:32] + b'\0' * 16 + data[48:]
        h.update(data)

        fo.seek(p, os.SEEK_SET)
        return h.digest()


class VmaDeviceInfoHeader():
    def __init__(self, fo, vma_header):
        self.__vma_header = vma_header

        # 0 -  3:   devive name (offsets into blob_buffer table)
        self.device_name = int.from_bytes(fo.read(4), 'big')

        # 4 -  7:   reserved
        fo.seek(4, os.SEEK_CUR)

        # 8 - 15:   device size in bytes
        self.device_size = int.from_bytes(fo.read(8), 'big')

        # 16 - 31:   reserved
        fo.seek(16, os.SEEK_CUR)


    def get_name(self):
        name = self.__vma_header.blob_buffer[self.device_name].data
        return name.split(b'\0')[0].decode('utf-8')


class VmaExtentHeader():
    def __init__(self, fo, vma_header, skip_hash):
        self.pos_start = fo.tell()

        # 0 -  3:   magic
        #     VMA extent magic string ("VMAE")
        magic = fo.read(4)
        assert magic == b'VMAE'

        # 4 -  5:   reserved
        fo.seek(2, os.SEEK_CUR)

        # 6 -  7:   block_count
        #     Overall number of contained 4K block
        self.block_count = int.from_bytes(fo.read(2), 'big')

        # 8 - 23:   uuid
        #     Unique ID, Same uuid as used in the VMA header.
        self.uuid = fo.read(16)

        # 24 - 39:   md5sum
        #     Header checksum (from byte 0 to header_size). This field
        #     is filled with zero to generate the checksum.
        self.md5sum = fo.read(16)

        # 40 - 511:   blockinfo[59]
        self.blockinfo = []
        for i in range(59):
            self.blockinfo.append(Blockinfo(fo, vma_header))

        self.pos_end = fo.tell()

        if skip_hash:
            self.generated_md5sum = None
        else:
            self.generated_md5sum = self.__gen_md5sum(fo)


    def __gen_md5sum(self, fo):
        p = fo.tell()
        fo.seek(self.pos_start, os.SEEK_SET)
        h = hashlib.md5()

        data = fo.read(self.pos_end - self.pos_start)
        data = data[:24] + b'\0' * 16 + data[40:]
        h.update(data)

        fo.seek(p, os.SEEK_SET)
        return h.digest()


class Blob():
    def __init__(self, fo):
        # the size of a blob is a two-byte int in LITTLE endian
        # source: original c code of vma-reader
        #    uint32_t size = vmar->head_data[bstart] +
        #        (vmar->head_data[bstart+1] << 8);
        self.size = int.from_bytes(fo.read(2), 'little')
        self.data = fo.read(self.size)


class Blockinfo():
    CLUSTER_SIZE = 65536

    def __init__(self, fo, vma_header):
        self.__vma_header = vma_header

        # 0 - 1:   mask
        self.mask = int.from_bytes(fo.read(2), 'big')

        # 2:   reserved
        fo.seek(1, os.SEEK_CUR)

        # 3:   dev_id
        #    Device ID (offset into dev_info table)
        self.dev_id = int.from_bytes(fo.read(1), 'big')

        # 4 - 7:   cluster_num
        self.cluster_num = int.from_bytes(fo.read(4), 'big')


def extract_configs(fo, args, vma_header):
    """
    Configs in VMA are composed of two blobs. One specifies the config's
    filename and the other contains the config's content.
    The filename seems to be a null-terminated string, while the content is not
    terminated.
    """

    if args.verbose: print('正在提取配置文件...')

    for i in range(256):
        if vma_header.config_names[i] == 0: continue
        config_name = vma_header.blob_buffer[vma_header.config_names[i]].data
        # interpret filename as a null-terminated utf-8 string
        config_name = config_name.split(b'\0')[0].decode('utf-8')

        if args.verbose: print(f'{config_name}...', end='')

        config_data = vma_header.blob_buffer[vma_header.config_data[i]].data

        with open(os.path.join(args.destination, config_name), 'wb') as config_fo:
            config_fo.write(config_data)

        if args.verbose: print(' 完成')


def extract(fo, args):
    os.makedirs(args.destination, exist_ok=True)
    output_format = getattr(args, 'output_format', OUTPUT_FORMAT_ORIGINAL)

    fo.seek(0, os.SEEK_END)
    filesize = fo.tell()
    fo.seek(0, os.SEEK_SET)

    vma_header = VmaHeader(fo, args.skip_hash)

    # check the md5 checksum given in the header with the value calculated from
    # the file
    if vma_header.generated_md5sum is not None:
        assert vma_header.md5sum == vma_header.generated_md5sum

    extract_configs(fo, args, vma_header)

    # extract_configs may move the read head somewhere into the blob buffer
    # make sure we are back at the end of the header
    fo.seek(vma_header.header_size, os.SEEK_SET)

    if args.verbose: print('正在提取磁盘设备...')

    # open file handlers for all devices within the VMA
    # so we can easily append data to arbitrary devices
    device_fos = {}
    device_paths = {}
    device_sizes = {}
    for dev_id, dev_info in enumerate(vma_header.dev_info):
        if dev_info.device_size > 0:
            device_name = dev_info.get_name()
            device_path = os.path.join(args.destination, device_output_name(device_name, output_format))
            if args.verbose: print(os.path.basename(device_path))
            device_paths[dev_id] = device_path
            device_sizes[dev_id] = dev_info.device_size
            device_fos[dev_id] = open(device_path, 'wb')
            enable_sparse_file(device_fos[dev_id])

    if args.verbose: print('这可能需要一些时间...')

    # used for sanity checking
    cluster_num_prev = -1
    non_sequential_count = 0
    skipped_zero_bytes = 0

    while(fo.tell() < filesize):
        # when there is data to read at this point, we can safely expect a full
        # extent header with additional clusters
        extent_header = VmaExtentHeader(fo, vma_header, args.skip_hash)
        assert vma_header.uuid == extent_header.uuid

        # check the md5 checksum given in the header with the value calculated from
        # the file
        if extent_header.generated_md5sum is not None:
            assert extent_header.md5sum == extent_header.generated_md5sum

        for blockinfo in extent_header.blockinfo:
            if blockinfo.dev_id == 0: continue

            device_fo = device_fos[blockinfo.dev_id]

            # non-sequential clusters encountered, handle this case
            if blockinfo.cluster_num != cluster_num_prev + 1:
                non_sequential_count += 1

                cluster_pos = blockinfo.cluster_num * Blockinfo.CLUSTER_SIZE
                if blockinfo.cluster_num > cluster_num_prev:
                    # special case: cluster num is larger than current,
                    # seek forward into file AND, if needed, fill missing size
                    # with zeros
                    device_fo.seek(0, os.SEEK_END)
                    written_size = device_fo.tell()

                    if written_size < cluster_pos:
                        skipped = cluster_pos - written_size
                        skipped_zero_bytes += skipped
                        skip_zeroes(device_fo, skipped)

                # seek to start of new cluster
                device_fo.seek(cluster_pos, os.SEEK_SET)

            cluster_num_prev = blockinfo.cluster_num

            for i in range(16):
                # a 2-bytes wide bitmask indicates 4k blocks with only zeros
                if (1 << i) & blockinfo.mask:
                    device_fo.write(fo.read(ZERO_BLOCK_SIZE))
                else:
                    skip_zeroes(device_fo, ZERO_BLOCK_SIZE)

    if args.verbose and non_sequential_count:
        skipped_gib = skipped_zero_bytes / 1024 / 1024 / 1024
        print(f'发现 {non_sequential_count} 个非连续簇，跳过零填充 {skipped_gib:.2f} GiB。')

    if args.verbose: print('正在关闭文件句柄...')
    for dev_id, device_fo in device_fos.items():
        device_fo.truncate(device_sizes[dev_id])
        device_fo.close()

    if args.verbose: print('完成')
    return list(device_paths.values())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('filename', type=str)
    parser.add_argument('destination', type=str)
    parser.add_argument('-v', '--verbose', default=False, action='store_true')
    parser.add_argument('-f', '--force', default=False, action='store_true',
            help='允许使用已存在的目标路径')
    parser.add_argument('--skip-hash', default=False, action='store_true',
            help='跳过 MD5 校验')
    parser.add_argument('--output-format', default=OUTPUT_FORMAT_ORIGINAL,
            choices=(OUTPUT_FORMAT_ORIGINAL, OUTPUT_FORMAT_RAW),
            help='磁盘设备输出命名格式')
    args = parser.parse_args()

    if(not os.path.exists(args.filename)):
        print('错误：源文件不存在！')
        return 1

    if(os.path.exists(args.destination) and not args.force):
        print('错误：目标路径已存在！')
        return 1

    with open(args.filename, 'rb') as fo:
        extract(fo, args)

    return 0

if __name__ == '__main__':
    sys.exit(main())
