"""
Tab for configuring and running NFS-e downloads.
"""

import os
import tkinter as tk
from datetime import date, timedelta
from tkinter import filedialog, messagebox
import customtkinter as ctk

from app.storage import list_clients
from app.models.client import Client
from app.services.downloader import DownloadJob


class DownloadTab(ctk.CTkFrame):
    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self._job: DownloadJob | None = None
        self._client_vars: dict[str, tk.BooleanVar] = {}
        self._build_ui()
        self._load_clients()

    # ─── Layout ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1, minsize=260)
        self.grid_columnconfigure(1, weight=2)
        self.grid_rowconfigure(0, weight=1)

        # Left — config panel
        left = ctk.CTkScrollableFrame(self)
        left.grid(row=0, column=0, sticky="nsew", padx=(10, 5), pady=10)
        left.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(left, text="Configurações",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=10, pady=(8, 4))

        # Clients
        ctk.CTkLabel(left, text="Clientes", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=10, pady=(10, 2)
        )
        sel_frame = ctk.CTkFrame(left, fg_color="transparent")
        sel_frame.pack(fill="x", padx=10, pady=(0, 4))
        ctk.CTkButton(sel_frame, text="Todos", width=70, height=24,
                      command=self._select_all).pack(side="left", padx=(0, 4))
        ctk.CTkButton(sel_frame, text="Nenhum", width=70, height=24,
                      command=self._select_none).pack(side="left")
        ctk.CTkButton(sel_frame, text="↺", width=36, height=24,
                      command=self._load_clients).pack(side="right")

        self.clients_frame = ctk.CTkFrame(left, fg_color=("gray90", "gray20"))
        self.clients_frame.pack(fill="x", padx=10, pady=(0, 10))

        # Period
        ctk.CTkLabel(left, text="Período", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=10, pady=(4, 2)
        )
        period_frame = ctk.CTkFrame(left, fg_color="transparent")
        period_frame.pack(fill="x", padx=10, pady=(0, 4))
        period_frame.grid_columnconfigure((0, 1, 2, 3), weight=1)

        today = date.today()
        first_of_month = today.replace(day=1)

        ctk.CTkLabel(period_frame, text="De").grid(row=0, column=0, sticky="w")
        self.e_data_ini = ctk.CTkEntry(period_frame, placeholder_text="AAAA-MM-DD")
        self.e_data_ini.grid(row=0, column=1, sticky="ew", padx=(4, 8))
        self.e_data_ini.insert(0, first_of_month.strftime("%Y-%m-%d"))

        ctk.CTkLabel(period_frame, text="Até").grid(row=0, column=2, sticky="w")
        self.e_data_fim = ctk.CTkEntry(period_frame, placeholder_text="AAAA-MM-DD")
        self.e_data_fim.grid(row=0, column=3, sticky="ew", padx=(4, 0))
        self.e_data_fim.insert(0, today.strftime("%Y-%m-%d"))

        # Shortcuts
        shortcuts = ctk.CTkFrame(left, fg_color="transparent")
        shortcuts.pack(fill="x", padx=10, pady=(0, 10))
        ctk.CTkButton(shortcuts, text="Este mês", height=26, width=80,
                      command=lambda: self._set_period("mes")).pack(side="left", padx=(0, 4))
        ctk.CTkButton(shortcuts, text="Mês anterior", height=26, width=100,
                      command=lambda: self._set_period("mes_ant")).pack(side="left", padx=(0, 4))
        ctk.CTkButton(shortcuts, text="Este ano", height=26, width=80,
                      command=lambda: self._set_period("ano")).pack(side="left")

        # NSU inicial
        ctk.CTkLabel(left, text="NSU Inicial (API Nacional)",
                     font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=10, pady=(4, 2))
        ctk.CTkLabel(left, text="0 = busca do início. Use um NSU maior para downloads parciais.",
                     font=ctk.CTkFont(size=11), text_color="gray").pack(anchor="w", padx=10)
        self.e_nsu = ctk.CTkEntry(left, placeholder_text="0")
        self.e_nsu.pack(fill="x", padx=10, pady=(2, 10))
        self.e_nsu.insert(0, "0")

        # Chaves manuais
        ctk.CTkLabel(left, text="Chaves de Acesso (opcional)",
                     font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=10, pady=(4, 2))
        ctk.CTkLabel(left, text="Uma chave por linha (deixe vazio para buscar por período)",
                     font=ctk.CTkFont(size=11), text_color="gray").pack(anchor="w", padx=10)
        self.txt_chaves = ctk.CTkTextbox(left, height=80)
        self.txt_chaves.pack(fill="x", padx=10, pady=(2, 10))

        # Ambiente
        ctk.CTkLabel(left, text="Ambiente (API Nacional)",
                     font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=10, pady=(4, 2))
        self.amb_var = ctk.StringVar(value="producao")
        amb_frame = ctk.CTkFrame(left, fg_color="transparent")
        amb_frame.pack(anchor="w", padx=10, pady=(0, 10))
        ctk.CTkRadioButton(amb_frame, text="Produção", variable=self.amb_var, value="producao").pack(
            side="left", padx=(0, 12)
        )
        ctk.CTkRadioButton(amb_frame, text="Homologação", variable=self.amb_var, value="homologacao").pack(
            side="left"
        )

        # Output folder
        ctk.CTkLabel(left, text="Pasta de Destino *",
                     font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=10, pady=(4, 2))
        out_frame = ctk.CTkFrame(left, fg_color="transparent")
        out_frame.pack(fill="x", padx=10, pady=(0, 10))
        out_frame.grid_columnconfigure(0, weight=1)
        self.e_output = ctk.CTkEntry(out_frame)
        self.e_output.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        default_out = os.path.join(os.path.expanduser("~"), "Desktop", "NFS-e Downloads")
        self.e_output.insert(0, default_out)
        ctk.CTkButton(out_frame, text="...", width=36,
                      command=self._browse_output).grid(row=0, column=1)

        # Download button
        self.btn_download = ctk.CTkButton(
            left, text="Baixar NFS-e", height=40,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._start_download
        )
        self.btn_download.pack(fill="x", padx=10, pady=(10, 4))

        self.btn_stop = ctk.CTkButton(
            left, text="Cancelar", height=36, fg_color="#c0392b", hover_color="#962d22",
            command=self._stop_download
        )
        self.btn_stop.pack(fill="x", padx=10, pady=(0, 10))
        self.btn_stop.configure(state="disabled")

        # Right — log
        right = ctk.CTkFrame(self)
        right.grid(row=0, column=1, sticky="nsew", padx=(5, 10), pady=10)
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        log_header = ctk.CTkFrame(right, fg_color="transparent")
        log_header.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 4))
        ctk.CTkLabel(log_header, text="Log de Execução",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        ctk.CTkButton(log_header, text="Limpar", width=70, height=26,
                      command=self._clear_log).pack(side="right")

        self.progress = ctk.CTkProgressBar(right)
        self.progress.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 4))
        self.progress.set(0)

        self.log_box = ctk.CTkTextbox(right, state="disabled", font=("Consolas", 11))
        self.log_box.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 10))

    # ─── Clients ────────────────────────────────────────────────────────────

    def _load_clients(self):
        for w in self.clients_frame.winfo_children():
            w.destroy()
        self._client_vars.clear()
        clients = list_clients()
        if not clients:
            ctk.CTkLabel(self.clients_frame,
                         text="Nenhum cliente cadastrado. Vá para a aba Clientes.",
                         text_color="gray").pack(padx=10, pady=8)
            return
        for c in clients:
            var = tk.BooleanVar(value=True)
            self._client_vars[c.id] = var
            ctk.CTkCheckBox(
                self.clients_frame,
                text=f"{c.nome}  ({c.cnpj_formatado})",
                variable=var
            ).pack(anchor="w", padx=8, pady=3)

    def _select_all(self):
        for v in self._client_vars.values():
            v.set(True)

    def _select_none(self):
        for v in self._client_vars.values():
            v.set(False)

    # ─── Period shortcuts ────────────────────────────────────────────────────

    def _set_period(self, period: str):
        today = date.today()
        if period == "mes":
            ini = today.replace(day=1)
            fim = today
        elif period == "mes_ant":
            fim = today.replace(day=1) - timedelta(days=1)
            ini = fim.replace(day=1)
        elif period == "ano":
            ini = today.replace(month=1, day=1)
            fim = today
        else:
            return
        self.e_data_ini.delete(0, "end")
        self.e_data_ini.insert(0, ini.strftime("%Y-%m-%d"))
        self.e_data_fim.delete(0, "end")
        self.e_data_fim.insert(0, fim.strftime("%Y-%m-%d"))

    # ─── Output ─────────────────────────────────────────────────────────────

    def _browse_output(self):
        path = filedialog.askdirectory(title="Selecionar pasta de destino")
        if path:
            self.e_output.delete(0, "end")
            self.e_output.insert(0, path)

    # ─── Log ────────────────────────────────────────────────────────────────

    def _log(self, msg: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")
        self.progress.set(0)

    # ─── Download ────────────────────────────────────────────────────────────

    def _start_download(self):
        # Validate inputs
        selected_ids = [cid for cid, var in self._client_vars.items() if var.get()]
        if not selected_ids:
            messagebox.showwarning("Atenção", "Selecione ao menos um cliente.")
            return

        try:
            data_ini = date.fromisoformat(self.e_data_ini.get().strip())
            data_fim = date.fromisoformat(self.e_data_fim.get().strip())
        except ValueError:
            messagebox.showerror("Data inválida", "Use o formato AAAA-MM-DD para as datas.")
            return

        output_dir = self.e_output.get().strip()
        if not output_dir:
            messagebox.showwarning("Atenção", "Informe a pasta de destino.")
            return

        all_clients = list_clients()
        clients = [c for c in all_clients if c.id in selected_ids]

        try:
            nsu_ini = int(self.e_nsu.get().strip() or "0")
        except ValueError:
            messagebox.showerror("NSU inválido", "O NSU inicial deve ser um número inteiro.")
            return

        chaves_raw = self.txt_chaves.get("1.0", "end").strip()
        chaves = [line.strip() for line in chaves_raw.splitlines() if line.strip()] if chaves_raw else []

        self._clear_log()
        self._log(f"Iniciando download para {len(clients)} cliente(s)...")
        self._log(f"Período: {data_ini} a {data_fim}")
        if nsu_ini > 0:
            self._log(f"NSU inicial: {nsu_ini}")
        self._log(f"Destino: {output_dir}\n")

        self.btn_download.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.progress.set(0)

        def on_progress(done, total):
            self.after(0, lambda: self.progress.set(done / total if total else 0))

        def on_done(ok, msg):
            self.after(0, lambda: self._on_done(ok, msg))

        self._job = DownloadJob(
            clients=clients,
            data_inicial=data_ini,
            data_final=data_fim,
            output_dir=output_dir,
            ambiente=self.amb_var.get(),
            nsu_inicial=nsu_ini,
            chaves_manuais=chaves,
            log_callback=lambda msg: self.after(0, lambda m=msg: self._log(m)),
            progress_callback=on_progress,
            done_callback=on_done,
        )
        self._job.run_in_thread()

    def _stop_download(self):
        if self._job:
            self._job.stop()

    def _on_done(self, ok: bool, msg: str):
        self.btn_download.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.progress.set(1.0 if ok else 0)
        if ok:
            messagebox.showinfo("Concluído", msg)
        else:
            messagebox.showerror("Erro", msg)

    def refresh(self):
        self._load_clients()
