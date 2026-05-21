VMA 提取工具
============

<p align="center">
  <img src="assets/vma-extractor.svg" width="360" alt="">
</p>

`vma.py` 是一个用于提取 [Proxmox](https://www.proxmox.com) VMA 备份格式的 Python 3 工具。桌面界面支持直接选择 `.vma`、`.vma.zst`、`.vma.gz` 或 `.vma.lzo`。

致谢：

本项目基于 [jancc/vma-extractor](https://github.com/jancc/vma-extractor) 的核心 VMA 解析与提取逻辑进行扩展。感谢原项目把这个工具以宽松许可证开源，让 Proxmox VMA 备份可以在非 Proxmox 环境中读取和恢复。

当前版本在原项目基础上增加了中文桌面界面、压缩备份支持、输出格式选项和 Windows exe 打包脚本；这些改动属于本仓库的后续增强，不代表原项目作者提供或背书。原始版权和许可声明请见 `LICENSE.md`。

贡献者：

- [Jan Wolff / jancc](https://github.com/jancc)：原项目 [jancc/vma-extractor](https://github.com/jancc/vma-extractor) 作者，提供核心 VMA 解析与提取实现。

命令行用法：
```sh
./vma.py path/to/source.vma path/to/target/directory
```

桌面界面：
```sh
python vma_gui.py
```

如果源文件是压缩的 VMA，桌面界面会先把它流式解压成临时 `.vma` 文件，再执行提取；提取结束后会自动删除临时文件。

压缩格式支持：

- `.vma.zst`：内置 `zstandard` 支持。
- `.vma.gz`：使用 Python 标准库解压。
- `.vma.lzo`：需要本机有 `lzop.exe`，可放入 PATH，或在界面里选择路径。

界面里的“输出格式”可以选择：

- 原始提取：保留 VMA 里的设备名。
- RAW 镜像：导出为 `.raw` 文件。
- QCOW2、VMDK、VHDX：先导出 RAW，再调用本机 `qemu-img.exe` 转换。

`qemu-img.exe` 和 `lzop.exe` 不会打进程序里，这样可以让最终 exe 尽量保持小体积。

用独立虚拟环境打包 Windows exe：
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_exe.ps1 -Clean
```

打包结果会输出到 `dist\VMAExtractor.exe`。

VMA 格式说明可以在 [git.proxmox.com](https://git.proxmox.com/?p=pve-qemu.git;a=blob_plain;f=vma_spec.txt;hb=refs/heads/master) 查看。
