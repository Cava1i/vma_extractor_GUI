VMA 提取工具
============

<p align="center">
  <img src="assets/vma-extractor.svg" width="360" alt="">
</p>

一个带中文图形界面的 Proxmox VMA 备份提取工具。核心 VMA 解析与提取逻辑基于 [jancc/vma-extractor](https://github.com/jancc/vma-extractor) 扩展，当前版本补充了 Windows 桌面界面、常见压缩格式处理、输出格式选择和 exe 打包脚本。

功能特性
--------

- 支持提取 `.vma` 备份文件。
- 支持直接选择 `.vma.zst`、`.vma.gz`、`.vma.lzo` 压缩备份。
- 支持输出为原始设备名、RAW、QCOW2、VMDK、VHDX。
- 支持 Windows 单文件 exe 打包。
- RAW 输出会尽量使用稀疏文件，减少真实磁盘占用。
- 大文件提取时限制日志刷新量，避免界面因日志过多假死。

桌面界面
--------

直接运行：

```sh
python vma_gui.py
```

或者使用发布页里的 `VMAExtractor.exe`。

压缩格式支持
------------

- `.vma.zst`：内置 `zstandard` 支持。
- `.vma.gz`：使用 Python 标准库解压。
- `.vma.lzo`：需要本机有 `lzop.exe`，可放入 PATH，或在界面里选择路径。

压缩备份会先流式解压成临时 `.vma` 文件，再执行提取；提取结束后会自动删除临时文件。因此目标盘需要有足够空间容纳临时 `.vma` 和最终输出文件。

输出格式
--------

- 原始提取：保留 VMA 中记录的设备名。
- RAW 镜像：导出 `.raw` 文件。
- QCOW2、VMDK、VHDX：先导出 RAW，再调用本机 `qemu-img.exe` 转换。

`qemu-img.exe` 不会打进程序里。需要 QCOW2、VMDK 或 VHDX 时，请把 `qemu-img.exe` 加入 PATH，或在界面里选择它的路径。

RAW 大小说明
------------

RAW 文件的“大小”通常等于虚拟机磁盘容量。例如 VM 磁盘配置为 60 GB，导出的 RAW 看起来就是 60 GB。

在 NTFS 等支持稀疏文件的文件系统上，实际“占用空间”可能远小于 60 GB。Windows 属性里可以看到两个值：

- 大小：镜像的逻辑容量。
- 占用空间：真实占用的磁盘空间。

所以 `.vma.zst` 只有几 GB，而 RAW 显示几十 GB 是正常现象；这是压缩备份展开为完整虚拟磁盘镜像后的结果。

命令行用法
----------

```sh
./vma.py path/to/source.vma path/to/target/directory
```

打包
----

用独立虚拟环境打包 Windows exe：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_exe.ps1 -Clean
```

打包结果会输出到：

```text
dist\VMAExtractor.exe
```

注意事项
--------

- 大备份提取可能耗时较长，建议输出到空间充足的 NTFS 磁盘。
- `.vma.lzo` 需要额外提供 `lzop.exe`。
- QCOW2、VMDK、VHDX 转换需要额外提供 `qemu-img.exe`。
- 如果一次提取被中断，建议删除半成品输出目录后重新提取。

致谢
----

本项目基于 [jancc/vma-extractor](https://github.com/jancc/vma-extractor) 的核心 VMA 解析与提取逻辑进行扩展。感谢原项目把这个工具以宽松许可证开源，让 Proxmox VMA 备份可以在非 Proxmox 环境中读取和恢复。

当前版本在原项目基础上增加了中文桌面界面、压缩备份支持、输出格式选项、稀疏 RAW 优化和 Windows exe 打包脚本；这些改动属于本仓库的后续增强，不代表原项目作者提供或背书。原始版权和许可声明请见 `LICENSE.md`。

贡献者
------

- [Jan Wolff / jancc](https://github.com/jancc)：原项目 [jancc/vma-extractor](https://github.com/jancc/vma-extractor) 作者，提供核心 VMA 解析与提取实现。

参考
----

VMA 格式说明可以在 [git.proxmox.com](https://git.proxmox.com/?p=pve-qemu.git;a=blob_plain;f=vma_spec.txt;hb=refs/heads/master) 查看。
