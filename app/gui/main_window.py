import customtkinter as ctk
from app.gui.clients_tab import ClientsTab
from app.gui.download_tab import DownloadTab

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class MainWindow(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("NFS-e Downloader — Escritório Contábil")
        self.geometry("1100x720")
        self.minsize(900, 600)

        # Top bar
        top = ctk.CTkFrame(self, height=48, corner_radius=0)
        top.pack(fill="x")
        ctk.CTkLabel(
            top,
            text="  NFS-e Downloader",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=("gray10", "white"),
        ).pack(side="left", padx=10, pady=8)
        ctk.CTkLabel(
            top,
            text="Sistema Nacional NFS-e  |  ABRASF SOAP",
            font=ctk.CTkFont(size=12),
            text_color="gray",
        ).pack(side="left", padx=0)

        # Tabs
        tabs = ctk.CTkTabview(self, command=self._on_tab_change)
        tabs.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        tabs.add("Baixar NFS-e")
        tabs.add("Clientes")

        self.download_tab = DownloadTab(tabs.tab("Baixar NFS-e"))
        self.download_tab.pack(fill="both", expand=True)

        self.clients_tab = ClientsTab(tabs.tab("Clientes"))
        self.clients_tab.pack(fill="both", expand=True)

    def _on_tab_change(self, tab: str):
        if tab == "Baixar NFS-e":
            self.download_tab.refresh()
        elif tab == "Clientes":
            self.clients_tab.refresh()
