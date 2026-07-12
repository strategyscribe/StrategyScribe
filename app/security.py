"""Voliteľné šifrovanie výstupného súboru verejným kľúčom (RSA, hybridné s
Fernet pre samotný obsah). StrategyScribe pozná len VEREJNÝ kľúč (vložený v
Nastaveniach) — tým sa dá iba šifrovať, nie dešifrovať. Výstup teda nevie
prečítať ani používateľ StrategyScribe, len ten, kto má zodpovedajúci
SÚKROMNÝ kľúč (Bot Z / Aurion). Súkromný kľúč sa sem nikdy nevkladá."""

import struct

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.fernet import Fernet

FILE_EXTENSION = ".ssenc"

_OAEP_PADDING = padding.OAEP(
    mgf=padding.MGF1(algorithm=hashes.SHA256()),
    algorithm=hashes.SHA256(),
    label=None,
)


def load_public_key(public_key_pem):
    """Načíta a overí verejný kľúč (PEM text). Vyhodí ValueError pri neplatnom kľúči."""
    return serialization.load_pem_public_key(
        public_key_pem.encode("utf-8"), backend=default_backend()
    )


def encrypt_text(text, public_key_pem):
    """Hybridné šifrovanie: náhodný jednorazový Fernet kľúč sa zašifruje RSA
    verejným kľúčom, samotný text sa zašifruje tým Fernet kľúčom (RSA samo
    o sebe nevie šifrovať dlhé texty). Vráti bajty na uloženie do súboru:
    [4B dĺžka RSA bloku][RSA-zašifrovaný Fernet kľúč][Fernet-zašifrovaný text]."""
    public_key = load_public_key(public_key_pem)
    fernet_key = Fernet.generate_key()
    encrypted_fernet_key = public_key.encrypt(fernet_key, _OAEP_PADDING)
    encrypted_content = Fernet(fernet_key).encrypt(text.encode("utf-8"))
    return struct.pack(">I", len(encrypted_fernet_key)) + encrypted_fernet_key + encrypted_content


def decrypt_text(encrypted_bytes, private_key_pem, private_key_password=None):
    """Opačná operácia — potrebuje SÚKROMNÝ kľúč. StrategyScribe túto funkciu
    nikdy nevolá (nemá a nesmie mať súkromný kľúč) — je tu len ako referencia
    pre implementáciu na strane Bot Z / Aurion."""
    private_key = serialization.load_pem_private_key(
        private_key_pem.encode("utf-8"),
        password=private_key_password.encode("utf-8") if private_key_password else None,
        backend=default_backend(),
    )
    key_len = struct.unpack(">I", encrypted_bytes[:4])[0]
    encrypted_fernet_key = encrypted_bytes[4:4 + key_len]
    encrypted_content = encrypted_bytes[4 + key_len:]
    fernet_key = private_key.decrypt(encrypted_fernet_key, _OAEP_PADDING)
    return Fernet(fernet_key).decrypt(encrypted_content).decode("utf-8")
