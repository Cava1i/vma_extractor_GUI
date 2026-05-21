#!/usr/bin/env python3
import contextlib
import gzip
import io
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import traceback
import tkinter as tk
from argparse import Namespace
from tkinter import filedialog, messagebox, ttk

from vma import OUTPUT_FORMAT_ORIGINAL, OUTPUT_FORMAT_RAW, extract

try:
    import zstandard as zstd
except ImportError:
    zstd = None


OUTPUT_FORMAT_OPTIONS = (
    ("原始提取（保留设备名）", OUTPUT_FORMAT_ORIGINAL),
    ("RAW 镜像（.raw）", OUTPUT_FORMAT_RAW),
    ("QCOW2 镜像（需要 qemu-img）", "qcow2"),
    ("VMDK 镜像（需要 qemu-img）", "vmdk"),
    ("VHDX 镜像（需要 qemu-img）", "vhdx"),
)
FORMAT_LABEL_TO_VALUE = dict(OUTPUT_FORMAT_OPTIONS)
FORMAT_VALUE_TO_LABEL = {value: label for label, value in OUTPUT_FORMAT_OPTIONS}
CONVERTED_FORMATS = {
    "qcow2": ".qcow2",
    "vmdk": ".vmdk",
    "vhdx": ".vhdx",
}
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
GZIP_MAGIC = b"\x1f\x8b"
LZOP_MAGIC = b"\x89LZO\x00\r\n\x1a\n"


class QueueWriter(io.TextIOBase):
    def __init__(self, output_queue):
        self.output_queue = output_queue

    def writable(self):
        return True

    def write(self, text):
        if text:
            self.output_queue.put(("log", text))
        return len(text)

    def flush(self):
        return None


class VmaExtractorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("VMA 提取工具")
        self.geometry("780x610")
        self.minsize(720, 560)

        self.output_queue = queue.Queue()
        self.worker = None

        self.source_var = tk.StringVar()
        self.destination_var = tk.StringVar()
        self.format_var = tk.StringVar(value=OUTPUT_FORMAT_OPTIONS[0][0])
        self.qemu_img_var = tk.StringVar(value=self._find_qemu_img() or "")
        self.lzop_var = tk.StringVar(value=self._find_lzop() or "")
        self.force_var = tk.BooleanVar(value=False)
        self.skip_hash_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="就绪")
        self.format_hint_var = tk.StringVar()

        self._configure_style()
        self._build_ui()
        self._update_format_hint()
        self.after(100, self._drain_queue)

    def _configure_style(self):
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")

        style.configure("TFrame", background="#f6f7f9")
        style.configure("Panel.TFrame", background="#ffffff")
        style.configure("Title.TLabel", background="#f6f7f9", foreground="#151922", font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("Body.TLabel", background="#f6f7f9", foreground="#4b5563", font=("Microsoft YaHei UI", 10))
        style.configure("Panel.TLabel", background="#ffffff", foreground="#1f2937", font=("Microsoft YaHei UI", 10))
        style.configure("Hint.TLabel", background="#ffffff", foreground="#6b7280", font=("Microsoft YaHei UI", 9))
        style.configure("Status.TLabel", background="#f6f7f9", foreground="#374151", font=("Microsoft YaHei UI", 9))
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("TCheckbutton", background="#ffffff", foreground="#1f2937", font=("Microsoft YaHei UI", 10))

    def _build_ui(self):
        self.configure(background="#f6f7f9")

        root = ttk.Frame(self, padding=18)
        root.pack(fill=tk.BOTH, expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        ttk.Label(root, text="VMA 提取工具", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(root, text="把 Proxmox .vma 备份解包到本地目录。", style="Body.TLabel").grid(
            row=1, column=0, sticky="w", pady=(2, 14)
        )

        panel = ttk.Frame(root, style="Panel.TFrame", padding=14)
        panel.grid(row=2, column=0, sticky="nsew")
        panel.columnconfigure(1, weight=1)
        panel.rowconfigure(8, weight=1)

        ttk.Label(panel, text="源文件", style="Panel.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.source_entry = ttk.Entry(panel, textvariable=self.source_var)
        self.source_entry.grid(row=0, column=1, sticky="ew")
        self.source_button = ttk.Button(panel, text="浏览", command=self._choose_source)
        self.source_button.grid(row=0, column=2, padx=(10, 0))

        ttk.Label(panel, text="目标目录", style="Panel.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=(10, 0))
        self.destination_entry = ttk.Entry(panel, textvariable=self.destination_var)
        self.destination_entry.grid(row=1, column=1, sticky="ew", pady=(10, 0))
        self.destination_button = ttk.Button(panel, text="浏览", command=self._choose_destination)
        self.destination_button.grid(row=1, column=2, padx=(10, 0), pady=(10, 0))

        ttk.Label(panel, text="输出格式", style="Panel.TLabel").grid(row=2, column=0, sticky="w", padx=(0, 10), pady=(10, 0))
        self.format_combo = ttk.Combobox(
            panel,
            textvariable=self.format_var,
            values=[label for label, _ in OUTPUT_FORMAT_OPTIONS],
            state="readonly",
        )
        self.format_combo.grid(row=2, column=1, sticky="ew", pady=(10, 0))
        self.format_combo.bind("<<ComboboxSelected>>", self._on_format_changed)

        ttk.Label(panel, text="qemu-img", style="Panel.TLabel").grid(row=3, column=0, sticky="w", padx=(0, 10), pady=(10, 0))
        self.qemu_img_entry = ttk.Entry(panel, textvariable=self.qemu_img_var)
        self.qemu_img_entry.grid(row=3, column=1, sticky="ew", pady=(10, 0))
        self.qemu_img_button = ttk.Button(panel, text="选择", command=self._choose_qemu_img)
        self.qemu_img_button.grid(row=3, column=2, padx=(10, 0), pady=(10, 0))

        ttk.Label(panel, text="lzop", style="Panel.TLabel").grid(row=4, column=0, sticky="w", padx=(0, 10), pady=(10, 0))
        self.lzop_entry = ttk.Entry(panel, textvariable=self.lzop_var)
        self.lzop_entry.grid(row=4, column=1, sticky="ew", pady=(10, 0))
        self.lzop_button = ttk.Button(panel, text="选择", command=self._choose_lzop)
        self.lzop_button.grid(row=4, column=2, padx=(10, 0), pady=(10, 0))

        ttk.Label(panel, textvariable=self.format_hint_var, style="Hint.TLabel").grid(
            row=5, column=1, columnspan=2, sticky="w", pady=(6, 0)
        )

        options = ttk.Frame(panel, style="Panel.TFrame")
        options.grid(row=6, column=1, columnspan=2, sticky="w", pady=(12, 4))
        self.force_check = ttk.Checkbutton(options, text="允许使用已有目录", variable=self.force_var)
        self.force_check.pack(side=tk.LEFT)
        self.skip_hash_check = ttk.Checkbutton(options, text="跳过 MD5 校验", variable=self.skip_hash_var)
        self.skip_hash_check.pack(side=tk.LEFT, padx=(18, 0))

        ttk.Label(panel, text="提取日志", style="Panel.TLabel").grid(row=7, column=0, columnspan=3, sticky="w", pady=(12, 6))
        log_frame = ttk.Frame(panel, style="Panel.TFrame")
        log_frame.grid(row=8, column=0, columnspan=3, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log_text = tk.Text(
            log_frame,
            height=12,
            wrap=tk.WORD,
            relief=tk.FLAT,
            borderwidth=1,
            background="#111827",
            foreground="#e5e7eb",
            insertbackground="#e5e7eb",
            font=("Consolas", 9),
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        self.log_text.configure(state=tk.DISABLED)
        scrollbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

        ttk.Label(panel, text="大备份会比较久；提取时窗口仍可操作。", style="Hint.TLabel").grid(
            row=9, column=0, columnspan=3, sticky="w", pady=(8, 0)
        )

        footer = ttk.Frame(root)
        footer.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        footer.columnconfigure(0, weight=1)

        ttk.Label(footer, textvariable=self.status_var, style="Status.TLabel").grid(row=0, column=0, sticky="w")
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=180)
        self.progress.grid(row=0, column=1, padx=(0, 12), sticky="e")
        self.extract_button = ttk.Button(footer, text="开始提取", style="Accent.TButton", command=self._start_extract)
        self.extract_button.grid(row=0, column=2, sticky="e")

    def _choose_source(self):
        filename = filedialog.askopenfilename(
            title="选择 VMA 备份",
            filetypes=(
                ("VMA 备份", "*.vma *.vma.zst *.vma.gz *.vma.lzo *.zst *.gz *.lzo"),
                ("VMA 文件", "*.vma"),
                ("压缩 VMA", "*.vma.zst *.vma.gz *.vma.lzo *.zst *.gz *.lzo"),
                ("所有文件", "*.*"),
            ),
        )
        if filename:
            self.source_var.set(filename)
            if not self.destination_var.get():
                base_name = os.path.splitext(os.path.basename(filename))[0] or "extracted"
                self.destination_var.set(os.path.join(os.path.dirname(filename), f"{base_name}_extracted"))

    def _choose_destination(self):
        directory = filedialog.askdirectory(title="选择目标目录")
        if directory:
            self.destination_var.set(directory)

    def _choose_qemu_img(self):
        filename = filedialog.askopenfilename(
            title="选择 qemu-img.exe",
            filetypes=(("qemu-img", "qemu-img.exe"), ("可执行文件", "*.exe"), ("所有文件", "*.*")),
        )
        if filename:
            self.qemu_img_var.set(filename)
            self._update_format_hint()

    def _choose_lzop(self):
        filename = filedialog.askopenfilename(
            title="选择 lzop.exe",
            filetypes=(("lzop", "lzop.exe"), ("可执行文件", "*.exe"), ("所有文件", "*.*")),
        )
        if filename:
            self.lzop_var.set(filename)

    def _on_format_changed(self, _event=None):
        self._update_format_hint()

    def _format_value(self):
        return FORMAT_LABEL_TO_VALUE.get(self.format_var.get(), OUTPUT_FORMAT_ORIGINAL)

    def _find_qemu_img(self):
        return shutil.which("qemu-img") or shutil.which("qemu-img.exe")

    def _find_lzop(self):
        return shutil.which("lzop") or shutil.which("lzop.exe")

    def _resolve_qemu_img(self):
        candidate = self.qemu_img_var.get().strip().strip('"')
        if candidate:
            if os.path.isfile(candidate):
                return candidate
            found = shutil.which(candidate)
            if found:
                return found
            return None
        return self._find_qemu_img()

    def _resolve_lzop(self):
        candidate = self.lzop_var.get().strip().strip('"')
        if candidate:
            if os.path.isfile(candidate):
                return candidate
            found = shutil.which(candidate)
            if found:
                return found
            return None
        return self._find_lzop()

    def _update_format_hint(self):
        output_format = self._format_value()
        if output_format in CONVERTED_FORMATS:
            qemu_img = self._resolve_qemu_img()
            if qemu_img:
                self.format_hint_var.set(f"将先导出 RAW，再用 qemu-img 转换为 {output_format.upper()}。")
            else:
                self.format_hint_var.set(f"{output_format.upper()} 需要 qemu-img.exe；可在这里选择路径。")
        elif output_format == OUTPUT_FORMAT_RAW:
            self.format_hint_var.set("磁盘设备会以 .raw 文件导出；配置文件保持原名。")
        else:
            self.format_hint_var.set("磁盘设备会按 VMA 中记录的原始设备名导出。")

    def _start_extract(self):
        if self.worker and self.worker.is_alive():
            return

        source = self.source_var.get().strip()
        destination = self.destination_var.get().strip()
        requested_format = self._format_value()
        qemu_img = None
        lzop = None

        if not source:
            messagebox.showwarning("需要源文件", "请先选择一个 VMA 备份文件。")
            return
        if not os.path.exists(source):
            messagebox.showerror("源文件不存在", "选择的源文件不存在。")
            return
        if not destination:
            messagebox.showwarning("需要目标目录", "请先选择目标目录。")
            return
        if os.path.exists(destination) and not self.force_var.get():
            messagebox.showerror("目标目录已存在", "请允许使用已有目录，或换一个新的目标目录。")
            return
        if self._compression_type(source) == "lzo":
            lzop = self._resolve_lzop()
            if not lzop:
                messagebox.showerror(
                    "缺少 lzop",
                    "LZO 压缩的 VMA 需要 lzop.exe。请把它加入 PATH，或在界面里选择 lzop.exe。",
                )
                return
        if requested_format in CONVERTED_FORMATS:
            qemu_img = self._resolve_qemu_img()
            if not qemu_img:
                messagebox.showerror(
                    "缺少 qemu-img",
                    "选择 QCOW2、VMDK 或 VHDX 时需要 qemu-img.exe。请把它加入 PATH，或在界面里选择 qemu-img.exe。",
                )
                return

        self._clear_log()
        self._set_running(True)

        args = Namespace(
            filename=source,
            destination=destination,
            verbose=True,
            force=self.force_var.get(),
            skip_hash=self.skip_hash_var.get(),
            output_format=OUTPUT_FORMAT_RAW if requested_format in CONVERTED_FORMATS else requested_format,
            requested_format=requested_format,
            qemu_img=qemu_img,
            lzop=lzop,
        )
        self.worker = threading.Thread(target=self._extract_worker, args=(source, args), daemon=True)
        self.worker.start()

    def _extract_worker(self, source, args):
        writer = QueueWriter(self.output_queue)
        format_label = FORMAT_VALUE_TO_LABEL.get(args.requested_format, args.requested_format)
        prepared_source = source
        temp_source = None
        try:
            self.output_queue.put(
                (
                    "log",
                    f"源文件: {source}\n目标目录: {args.destination}\n输出格式: {format_label}\n\n",
                )
            )
            with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                prepared_source, temp_source = self._prepare_source(source, args)
                with open(prepared_source, "rb") as fo:
                    device_paths = extract(fo, args)
                if args.requested_format in CONVERTED_FORMATS:
                    self._convert_devices(device_paths, args)
            self.output_queue.put(("done", "提取完成。"))
        except Exception:
            self.output_queue.put(("error", traceback.format_exc()))
        finally:
            if temp_source:
                with contextlib.suppress(FileNotFoundError):
                    os.remove(temp_source)

    def _prepare_source(self, source, args):
        compression_type = self._compression_type(source)
        if compression_type is None:
            return source, None

        if compression_type == "zst":
            temp_source = self._decompress_zstd(source, args.destination)
        elif compression_type == "gz":
            temp_source = self._decompress_gzip(source, args.destination)
        elif compression_type == "lzo":
            temp_source = self._decompress_lzo(source, args.destination, args.lzop)
        else:
            raise ValueError(f"不支持的压缩格式：{compression_type}")

        with open(temp_source, "rb") as fo:
            if fo.read(4) != b"VMA\0":
                raise ValueError(f"{compression_type} 解压完成，但解压后的内容不是有效的 VMA 文件。")
        return temp_source, temp_source

    def _compression_type(self, source):
        source_lower = source.lower()
        with contextlib.suppress(OSError):
            with open(source, "rb") as fo:
                magic = fo.read(len(LZOP_MAGIC))
            if magic.startswith(ZSTD_MAGIC):
                return "zst"
            if magic.startswith(GZIP_MAGIC):
                return "gz"
            if magic.startswith(LZOP_MAGIC):
                return "lzo"

        if source_lower.endswith(".zst"):
            return "zst"
        if source_lower.endswith((".gz", ".tgz")):
            return "gz"
        if source_lower.endswith(".lzo"):
            return "lzo"
        return None

    def _create_temp_vma(self, destination):
        os.makedirs(destination, exist_ok=True)
        temp_file = tempfile.NamedTemporaryFile(
            mode="wb",
            suffix=".vma",
            prefix=".vma_extract_",
            dir=destination,
            delete=False,
        )
        temp_path = temp_file.name
        temp_file.close()
        return temp_path

    def _decompress_zstd(self, source, destination):
        if zstd is None:
            raise RuntimeError("当前程序缺少 zstandard 支持，无法解压 .vma.zst。请重新打包，或先手动解压为 .vma。")

        temp_path = self._create_temp_vma(destination)

        print("检测到 zstd 压缩的 VMA，正在解压到临时文件...")
        try:
            decompressor = zstd.ZstdDecompressor()
            with open(source, "rb") as source_fo, open(temp_path, "wb") as target_fo:
                with decompressor.stream_reader(source_fo) as reader:
                    shutil.copyfileobj(reader, target_fo, length=1024 * 1024)
            size_gb = os.path.getsize(temp_path) / 1024 / 1024 / 1024
            print(f"临时 VMA 已解压：{os.path.basename(temp_path)} ({size_gb:.2f} GB)")
            return temp_path
        except Exception:
            with contextlib.suppress(FileNotFoundError):
                os.remove(temp_path)
            raise

    def _decompress_gzip(self, source, destination):
        temp_path = self._create_temp_vma(destination)
        print("检测到 gzip 压缩的 VMA，正在解压到临时文件...")
        try:
            with gzip.open(source, "rb") as source_fo, open(temp_path, "wb") as target_fo:
                shutil.copyfileobj(source_fo, target_fo, length=1024 * 1024)
            size_gb = os.path.getsize(temp_path) / 1024 / 1024 / 1024
            print(f"临时 VMA 已解压：{os.path.basename(temp_path)} ({size_gb:.2f} GB)")
            return temp_path
        except Exception:
            with contextlib.suppress(FileNotFoundError):
                os.remove(temp_path)
            raise

    def _decompress_lzo(self, source, destination, lzop):
        if not lzop:
            raise RuntimeError("当前程序找不到 lzop.exe，无法解压 .vma.lzo。")

        temp_path = self._create_temp_vma(destination)
        print("检测到 LZO 压缩的 VMA，正在通过 lzop 解压到临时文件...")
        try:
            with open(temp_path, "wb") as target_fo:
                result = subprocess.run(
                    [lzop, "-d", "-c", source],
                    stdout=target_fo,
                    stderr=subprocess.PIPE,
                    text=False,
                )
            if result.returncode != 0:
                stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
                raise RuntimeError(f"lzop 解压失败，退出码 {result.returncode}\n{stderr}")
            if result.stderr:
                print(result.stderr.decode("utf-8", errors="replace"), end="")
            size_gb = os.path.getsize(temp_path) / 1024 / 1024 / 1024
            print(f"临时 VMA 已解压：{os.path.basename(temp_path)} ({size_gb:.2f} GB)")
            return temp_path
        except Exception:
            with contextlib.suppress(FileNotFoundError):
                os.remove(temp_path)
            raise

    def _convert_devices(self, device_paths, args):
        if not device_paths:
            print("\n没有发现磁盘设备，跳过格式转换。")
            return

        print(f"\n正在转换磁盘格式为 {args.requested_format.upper()}...")
        converted_paths = []
        for raw_path in device_paths:
            output_path = self._converted_path(raw_path, args.requested_format)
            if os.path.exists(output_path):
                if args.force:
                    os.remove(output_path)
                else:
                    raise FileExistsError(f"目标文件已存在: {output_path}")

            print(f"{os.path.basename(raw_path)} -> {os.path.basename(output_path)}")
            command = [
                args.qemu_img,
                "convert",
                "-f",
                "raw",
                "-O",
                args.requested_format,
                raw_path,
                output_path,
            ]
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if result.stdout:
                print(result.stdout, end="")
            if result.stderr:
                print(result.stderr, end="")
            if result.returncode != 0:
                raise RuntimeError(f"qemu-img 转换失败，退出码 {result.returncode}")
            converted_paths.append(output_path)

        for raw_path in device_paths:
            with contextlib.suppress(FileNotFoundError):
                os.remove(raw_path)
        print("格式转换完成。")
        for output_path in converted_paths:
            print(os.path.basename(output_path))

    def _converted_path(self, raw_path, output_format):
        extension = CONVERTED_FORMATS[output_format]
        if raw_path.lower().endswith(".raw"):
            return f"{raw_path[:-4]}{extension}"
        return f"{raw_path}{extension}"

    def _drain_queue(self):
        try:
            while True:
                event, payload = self.output_queue.get_nowait()
                if event == "log":
                    self._append_log(payload)
                elif event == "done":
                    self._append_log(f"\n{payload}\n")
                    self.status_var.set(payload)
                    self._set_running(False)
                    messagebox.showinfo("完成", payload)
                elif event == "error":
                    self._append_log(f"\n{payload}\n")
                    self.status_var.set("提取失败。")
                    self._set_running(False)
                    messagebox.showerror("提取失败", "详情请查看日志。")
        except queue.Empty:
            pass
        self.after(100, self._drain_queue)

    def _append_log(self, text):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, text)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _clear_log(self):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _set_running(self, running):
        state = tk.DISABLED if running else tk.NORMAL
        readonly_state = tk.DISABLED if running else "readonly"
        if running:
            self.status_var.set("正在提取...")
            self.progress.start(12)
        else:
            self.progress.stop()

        self.extract_button.configure(state=state)
        self.source_button.configure(state=state)
        self.destination_button.configure(state=state)
        self.qemu_img_button.configure(state=state)
        self.lzop_button.configure(state=state)
        self.source_entry.configure(state=state)
        self.destination_entry.configure(state=state)
        self.qemu_img_entry.configure(state=state)
        self.lzop_entry.configure(state=state)
        self.format_combo.configure(state=readonly_state)
        self.force_check.configure(state=state)
        self.skip_hash_check.configure(state=state)


def main():
    app = VmaExtractorApp()
    app.mainloop()


if __name__ == "__main__":
    main()
