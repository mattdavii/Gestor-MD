import os
import datetime
from typing import Optional

import jwt
import bcrypt
from fastapi import Header, HTTPException

# --- CONFIGURAÇÃO ---
# Defina JWT_SECRET como env var no Render. Nunca deixe um valor fixo em produção.
JWT_SECRET = os.environ.get("JWT_SECRET", "troque-esta-chave-em-producao")
JWT_ALGORITHM = "HS256"
JWT_EXP_HORAS = 12


def hash_senha(senha_pura: str) -> str:
    return bcrypt.hashpw(senha_pura.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verificar_senha(senha_pura: str, senha_hash: str) -> bool:
    try:
        return bcrypt.checkpw(senha_pura.encode("utf-8"), senha_hash.encode("utf-8"))
    except Exception:
        return False


def senha_parece_hash(valor: str) -> bool:
    """Detecta se um valor no banco já está em formato bcrypt (para migração)."""
    return valor.startswith("$2b$") or valor.startswith("$2a$") or valor.startswith("$2y$")


def criar_token(user_id: int, perfil: str) -> str:
    payload = {
        "sub": str(user_id),
        "perfil": perfil,
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=JWT_EXP_HORAS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decodificar_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Sessão expirada, faça login novamente.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token inválido.")


def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    """Dependency do FastAPI: exige header 'Authorization: Bearer <token>'."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Não autenticado.")
    token = authorization.split(" ", 1)[1]
    payload = decodificar_token(token)
    return {"id": int(payload["sub"]), "perfil": payload["perfil"]}


def exigir_admin(user: dict = None):
    """Use como segunda dependency nas rotas restritas a Admin."""
    if user is None or user.get("perfil") != "Admin":
        raise HTTPException(status_code=403, detail="Acesso restrito a administradores.")
    return user
