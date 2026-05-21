# v1.0.0 - 中文图形界面版本

这是一个带中文图形界面的 Proxmox VMA 备份提取工具，基于 [jancc/vma-extractor](https://github.com/jancc/vma-extractor) 的核心 VMA 解析与提取逻辑扩展而来。

## 功能

- 支持提取 `.vma` 备份文件。
- 支持 `.vma.zst`、`.vma.gz` 压缩备份自动解压。
- 支持 `.vma.lzo`，需要本机提供 `lzop.exe`。
- 支持输出格式选择：
  - 原始提取
  - RAW
  - QCOW2
  - VMDK
  - VHDX
- QCOW2、VMDK、VHDX 转换需要本机提供 `qemu-img.exe`。
- RAW 输出会尽量使用稀疏文件，减少真实磁盘占用。
- 优化大文件提取时的日志输出，避免界面因日志过多假死。
- 提供 Windows 单文件 exe 打包版本。

## 使用说明

1. 下载并运行 `VMAExtractor.exe`。
2. 选择 `.vma`、`.vma.zst`、`.vma.gz` 或 `.vma.lzo` 备份文件。
3. 选择目标目录。
4. 选择输出格式。
5. 点击“开始提取”。

## 注意事项

- 压缩备份会先解压为临时 `.vma` 文件，因此目标盘需要有足够空间。
- RAW 文件显示的“大小”通常等于虚拟机磁盘容量；实际占用请看 Windows 属性里的“占用空间”。
- `.vma.lzo` 需要安装或指定 `lzop.exe`。
- QCOW2、VMDK、VHDX 转换需要安装或指定 `qemu-img.exe`。
- 如果一次提取被中断，建议删除半成品输出目录后重新提取。

## 致谢

感谢 [Jan Wolff / jancc](https://github.com/jancc) 开源原始项目 [jancc/vma-extractor](https://github.com/jancc/vma-extractor)，本工具的核心 VMA 解析与提取逻辑基于该项目扩展。
