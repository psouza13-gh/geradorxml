from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Client:
    id: str
    nome: str
    cnpj: str
    inscricao_municipal: str = ""
    cert_path: str = ""
    cert_password: str = ""
    municipio_codigo: str = ""
    municipio_nome: str = ""
    api_tipo: str = "nacional"      # "nacional" | "abrasf"
    abrasf_url: str = ""           # URL do webservice ABRASF (quando api_tipo=abrasf)
    ativo: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Client":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    @property
    def cnpj_formatado(self) -> str:
        c = self.cnpj.replace(".", "").replace("/", "").replace("-", "")
        if len(c) == 14:
            return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}"
        return self.cnpj
