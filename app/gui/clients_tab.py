"""
Tab for managing client configurations.
"""

import os
import tkinter as tk
import customtkinter as ctk
from tkinter import filedialog, messagebox
from app.models.client import Client
from app.storage import list_clients, save_client, delete_client, new_client
from app.services.cert_handler import get_cert_info


class ClientsTab(ctk.CTkFrame):
    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self._selected_client: Client | None = None
        self._clients: list[Client] = []
        self._build_ui()
        self._load_list()

    # ─── Layout ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1, minsize=240)
        self.grid_columnconfigure(1, weight=3)
        self.grid_rowconfigure(0, weight=1)

        # Left panel — client list
        left = ctk.CTkFrame(self)
        left.grid(row=0, column=0, sticky="nsew", padx=(10, 5), pady=10)
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(left, text="Clientes", font=ctk.CTkFont(size=14, weight="bold")).grid(
            row=0, column=0, padx=10, pady=(10, 5), sticky="w"
        )

        self.listbox = tk.Listbox(
            left, bg="#2b2b2b", fg="white", selectbackground="#1f6aa5",
            relief="flat", font=("Segoe UI", 11), activestyle="none", borderwidth=0,
        )
        self.listbox.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)

        btn_frame = ctk.CTkFrame(left, fg_color="transparent")
        btn_frame.grid(row=2, column=0, padx=5, pady=5, sticky="ew")
        ctk.CTkButton(btn_frame, text="+ Novo", width=90, command=self._new_client).pack(side="left", padx=2)
        ctk.CTkButton(btn_frame, text="Excluir", width=90, fg_color="#c0392b",
                      hover_color="#962d22", command=self._delete_client).pack(side="left", padx=2)

        # Right panel — form
        right = ctk.CTkScrollableFrame(self)
        right.grid(row=0, column=1, sticky="nsew", padx=(5, 10), pady=10)
        right.grid_columnconfigure(1, weight=1)

        self._form_frame = right
        self._build_form(right)

    def _field(self, parent, label: str, row: int, show: str = "") -> ctk.CTkEntry:
        ctk.CTkLabel(parent, text=label, anchor="w").grid(row=row, column=0, sticky="w", padx=(10, 5), pady=4)
        entry = ctk.CTkEntry(parent, show=show)
        entry.grid(row=row, column=1, sticky="ew", padx=(0, 10), pady=4)
        return entry

    def _build_form(self, parent):
        ctk.CTkLabel(parent, text="Dados do Cliente",
                     font=ctk.CTkFont(size=14, weight="bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(10, 6)
        )

        self.e_nome = self._field(parent, "Nome / Razão Social *", 1)
        self.e_cnpj = self._field(parent, "CNPJ *", 2)
        self.e_im = self._field(parent, "Inscrição Municipal", 3)

        # API type
        ctk.CTkLabel(parent, text="Tipo de API *", anchor="w").grid(row=4, column=0, sticky="w", padx=(10, 5), pady=4)
        self.api_var = ctk.StringVar(value="nacional")
        api_frame = ctk.CTkFrame(parent, fg_color="transparent")
        api_frame.grid(row=4, column=1, sticky="w", pady=4)
        ctk.CTkRadioButton(api_frame, text="Nacional (REST)", variable=self.api_var,
                           value="nacional", command=self._toggle_abrasf).pack(side="left", padx=(0, 10))
        ctk.CTkRadioButton(api_frame, text="Municipal (ABRASF SOAP)", variable=self.api_var,
                           value="abrasf", command=self._toggle_abrasf).pack(side="left")

        # ABRASF URL (shown only when abrasf selected)
        self.lbl_abrasf = ctk.CTkLabel(parent, text="URL Webservice ABRASF", anchor="w")
        self.e_abrasf_url = ctk.CTkEntry(parent)
        self.lbl_abrasf.grid(row=5, column=0, sticky="w", padx=(10, 5), pady=4)
        self.e_abrasf_url.grid(row=5, column=1, sticky="ew", padx=(0, 10), pady=4)
        self.lbl_abrasf.grid_remove()
        self.e_abrasf_url.grid_remove()

        ctk.CTkLabel(parent, text="Certificado Digital",
                     font=ctk.CTkFont(size=13, weight="bold")).grid(
            row=6, column=0, columnspan=2, sticky="w", padx=10, pady=(14, 4)
        )

        # Certificate path
        ctk.CTkLabel(parent, text="Arquivo .pfx / .p12 *", anchor="w").grid(row=7, column=0, sticky="w", padx=(10, 5), pady=4)
        cert_row = ctk.CTkFrame(parent, fg_color="transparent")
        cert_row.grid(row=7, column=1, sticky="ew", padx=(0, 10), pady=4)
        cert_row.grid_columnconfigure(0, weight=1)
        self.e_cert = ctk.CTkEntry(cert_row)
        self.e_cert.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        ctk.CTkButton(cert_row, text="Procurar", width=80, command=self._browse_cert).grid(row=0, column=1)

        self.e_cert_pass = self._field(parent, "Senha do Certificado *", 8, show="*")

        # Cert info / validate
        self.lbl_cert_info = ctk.CTkLabel(parent, text="", anchor="w", text_color="gray")
        self.lbl_cert_info.grid(row=9, column=0, columnspan=2, sticky="w", padx=10, pady=0)
        ctk.CTkButton(parent, text="Verificar Certificado", command=self._verify_cert).grid(
            row=10, column=1, sticky="e", padx=10, pady=(0, 10)
        )

        # Município
        ctk.CTkLabel(parent, text="Município",
                     font=ctk.CTkFont(size=13, weight="bold")).grid(
            row=11, column=0, columnspan=2, sticky="w", padx=10, pady=(14, 4)
        )
        self.e_mun_cod = self._field(parent, "Código IBGE", 12)
        self.e_mun_nome = self._field(parent, "Nome do Município", 13)

        # Save button
        ctk.CTkButton(parent, text="Salvar Cliente", command=self._save_client,
                      font=ctk.CTkFont(weight="bold")).grid(
            row=14, column=0, columnspan=2, pady=(20, 10), padx=10, sticky="ew"
        )

    # ─── Helpers ────────────────────────────────────────────────────────────

    def _toggle_abrasf(self):
        if self.api_var.get() == "abrasf":
            self.lbl_abrasf.grid()
            self.e_abrasf_url.grid()
        else:
            self.lbl_abrasf.grid_remove()
            self.e_abrasf_url.grid_remove()

    def _browse_cert(self):
        path = filedialog.askopenfilename(
            title="Selecionar Certificado Digital",
            filetypes=[("Certificado Digital", "*.pfx *.p12"), ("Todos", "*.*")]
        )
        if path:
            self.e_cert.delete(0, "end")
            self.e_cert.insert(0, path)
            self.lbl_cert_info.configure(text="")

    def _verify_cert(self):
        path = self.e_cert.get().strip()
        password = self.e_cert_pass.get()
        if not path:
            messagebox.showwarning("Atenção", "Selecione um arquivo de certificado primeiro.")
            return
        try:
            info = get_cert_info(path, password)
            status = "VENCIDO" if info["vencido"] else "Válido"
            color = "#e74c3c" if info["vencido"] else "#2ecc71"
            self.lbl_cert_info.configure(
                text=f"CN: {info['cn']} | Validade: {info['validade']} [{status}]",
                text_color=color
            )
        except Exception as e:
            self.lbl_cert_info.configure(text=f"Erro: {e}", text_color="#e74c3c")

    # ─── CRUD ───────────────────────────────────────────────────────────────

    def _load_list(self):
        self._clients = list_clients()
        self.listbox.delete(0, "end")
        for c in self._clients:
            self.listbox.insert("end", f"  {c.nome or '(sem nome)'} — {c.cnpj_formatado}")
        if self._clients and self._selected_client:
            ids = [c.id for c in self._clients]
            if self._selected_client.id in ids:
                idx = ids.index(self._selected_client.id)
                self.listbox.selection_set(idx)

    def _on_select(self, event=None):
        sel = self.listbox.curselection()
        if not sel:
            return
        self._selected_client = self._clients[sel[0]]
        self._populate_form(self._selected_client)

    def _populate_form(self, client: Client):
        def set_entry(entry, value):
            entry.delete(0, "end")
            entry.insert(0, value or "")

        set_entry(self.e_nome, client.nome)
        set_entry(self.e_cnpj, client.cnpj)
        set_entry(self.e_im, client.inscricao_municipal)
        set_entry(self.e_cert, client.cert_path)
        set_entry(self.e_cert_pass, client.cert_password)
        set_entry(self.e_mun_cod, client.municipio_codigo)
        set_entry(self.e_mun_nome, client.municipio_nome)
        set_entry(self.e_abrasf_url, client.abrasf_url)
        self.api_var.set(client.api_tipo)
        self._toggle_abrasf()
        self.lbl_cert_info.configure(text="")

    def _new_client(self):
        self._selected_client = new_client()
        self._populate_form(self._selected_client)
        self.listbox.selection_clear(0, "end")

    def _save_client(self):
        if self._selected_client is None:
            self._selected_client = new_client()

        nome = self.e_nome.get().strip()
        cnpj = self.e_cnpj.get().strip()
        if not nome or not cnpj:
            messagebox.showwarning("Campos obrigatórios", "Preencha Nome e CNPJ.")
            return

        self._selected_client.nome = nome
        self._selected_client.cnpj = cnpj
        self._selected_client.inscricao_municipal = self.e_im.get().strip()
        self._selected_client.cert_path = self.e_cert.get().strip()
        self._selected_client.cert_password = self.e_cert_pass.get()
        self._selected_client.municipio_codigo = self.e_mun_cod.get().strip()
        self._selected_client.municipio_nome = self.e_mun_nome.get().strip()
        self._selected_client.api_tipo = self.api_var.get()
        self._selected_client.abrasf_url = self.e_abrasf_url.get().strip()

        save_client(self._selected_client)
        self._load_list()
        messagebox.showinfo("Salvo", f"Cliente '{nome}' salvo com sucesso.")

    def _delete_client(self):
        if self._selected_client is None:
            return
        if messagebox.askyesno("Confirmar", f"Excluir '{self._selected_client.nome}'?"):
            delete_client(self._selected_client.id)
            self._selected_client = None
            self._load_list()

    def refresh(self):
        self._load_list()
