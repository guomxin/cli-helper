from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import ipaddress
import json
import os
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


_ROOT_KEY_CONTEXT = b"agentbridge-internal-ca-root-v1"
_ROOT_CERTIFICATE_FILE = "root-ca.crt"
_ROOT_PRIVATE_KEY_FILE = "root-ca.key.dpapi"


class SecretProtector(Protocol):
    def protect(self, plaintext: bytes, *, context: bytes) -> bytes: ...

    def unprotect(self, ciphertext: bytes, *, context: bytes) -> bytes: ...


@dataclass(frozen=True)
class IssuedServerCertificate:
    created_root_ca: bool
    root_certificate_path: Path
    root_fingerprint_sha256: str
    root_not_after: str
    server_certificate_path: Path
    server_private_key_path: Path
    server_fingerprint_sha256: str
    server_ip: str
    server_not_after: str

    def as_dict(self) -> dict[str, object]:
        return {
            "status": "issued",
            "createdRootCa": self.created_root_ca,
            "rootCertificate": str(self.root_certificate_path),
            "rootFingerprintSha256": self.root_fingerprint_sha256,
            "rootNotAfter": self.root_not_after,
            "serverCertificate": str(self.server_certificate_path),
            "serverPrivateKey": str(self.server_private_key_path),
            "serverFingerprintSha256": self.server_fingerprint_sha256,
            "serverIp": self.server_ip,
            "serverNotAfter": self.server_not_after,
        }


class InternalCertificateAuthorityStore:
    """DPAPI-backed root CA storage with leaf-only deployment output."""

    def __init__(self, root: str | Path, protector: SecretProtector):
        self.root = Path(root).resolve()
        self.protector = protector

    @property
    def certificate_path(self) -> Path:
        return self.root / _ROOT_CERTIFICATE_FILE

    @property
    def protected_private_key_path(self) -> Path:
        return self.root / _ROOT_PRIVATE_KEY_FILE

    def issue_server_certificate(
        self,
        *,
        server_ip: str,
        output_dir: str | Path,
        root_common_name: str = "AgentBridge Internal Root CA",
        root_valid_days: int = 3650,
        server_valid_days: int = 397,
        force: bool = False,
        now: datetime | None = None,
    ) -> IssuedServerCertificate:
        ip = ipaddress.ip_address(server_ip)
        if not isinstance(ip, ipaddress.IPv4Address):
            raise ValueError("AgentBridge internal TLS currently requires an IPv4 address")
        if root_valid_days < 365 or root_valid_days > 7300:
            raise ValueError("root CA validity must be between 365 and 7300 days")
        if server_valid_days < 1 or server_valid_days > 397:
            raise ValueError("server certificate validity must be between 1 and 397 days")
        common_name = root_common_name.strip()
        if not common_name or len(common_name) > 120:
            raise ValueError("root CA common name is invalid")

        moment = _utc_now(now)
        root_certificate, root_private_key, created = self._load_or_create_root(
            common_name=common_name,
            valid_days=root_valid_days,
            now=moment,
        )
        if root_certificate.not_valid_after_utc <= moment + timedelta(days=server_valid_days):
            raise ValueError("root CA expires before the requested server certificate")

        output = Path(output_dir).resolve()
        if output == self.root:
            raise ValueError("server output directory must differ from root CA state directory")
        certificate_path = output / "server.crt"
        private_key_path = output / "server.key"
        root_copy_path = output / _ROOT_CERTIFICATE_FILE
        manifest_path = output / "manifest.json"
        targets = (certificate_path, private_key_path, root_copy_path, manifest_path)
        if not force and any(path.exists() for path in targets):
            raise FileExistsError("server certificate output already exists; pass force to replace it")

        server_key = ec.generate_private_key(ec.SECP256R1())
        server_certificate = _build_server_certificate(
            root_certificate=root_certificate,
            root_private_key=root_private_key,
            server_private_key=server_key,
            server_ip=ip,
            valid_days=server_valid_days,
            now=moment,
        )
        root_pem = root_certificate.public_bytes(serialization.Encoding.PEM)
        certificate_pem = server_certificate.public_bytes(serialization.Encoding.PEM)
        private_key_pem = server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        result = IssuedServerCertificate(
            created_root_ca=created,
            root_certificate_path=root_copy_path,
            root_fingerprint_sha256=_certificate_fingerprint(root_certificate),
            root_not_after=root_certificate.not_valid_after_utc.isoformat(),
            server_certificate_path=certificate_path,
            server_private_key_path=private_key_path,
            server_fingerprint_sha256=_certificate_fingerprint(server_certificate),
            server_ip=str(ip),
            server_not_after=server_certificate.not_valid_after_utc.isoformat(),
        )
        output.mkdir(parents=True, exist_ok=True)
        _atomic_write(certificate_path, certificate_pem, mode=0o644)
        _atomic_write(private_key_path, private_key_pem, mode=0o600)
        _atomic_write(root_copy_path, root_pem, mode=0o644)
        _atomic_write(
            manifest_path,
            json.dumps(result.as_dict(), ensure_ascii=False, indent=2).encode("utf-8") + b"\n",
            mode=0o600,
        )
        return result

    def _load_or_create_root(
        self,
        *,
        common_name: str,
        valid_days: int,
        now: datetime,
    ) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey, bool]:
        certificate_exists = self.certificate_path.exists()
        key_exists = self.protected_private_key_path.exists()
        if certificate_exists != key_exists:
            raise ValueError("internal CA state is incomplete")
        if certificate_exists:
            certificate, private_key = self._load_root()
            common_names = certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
            if not common_names or common_names[0].value != common_name:
                raise ValueError("internal CA common name does not match existing state")
            return certificate, private_key, False

        private_key = ec.generate_private_key(ec.SECP256R1())
        certificate = _build_root_certificate(
            private_key=private_key,
            common_name=common_name,
            valid_days=valid_days,
            now=now,
        )
        private_key_pem = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        protected = self.protector.protect(private_key_pem, context=_ROOT_KEY_CONTEXT)
        self.root.mkdir(parents=True, exist_ok=True)
        _atomic_write(
            self.certificate_path,
            certificate.public_bytes(serialization.Encoding.PEM),
            mode=0o644,
        )
        _atomic_write(self.protected_private_key_path, protected, mode=0o600)
        return certificate, private_key, True

    def _load_root(self) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
        certificate = x509.load_pem_x509_certificate(self.certificate_path.read_bytes())
        protected = self.protected_private_key_path.read_bytes()
        private_key_pem = self.protector.unprotect(protected, context=_ROOT_KEY_CONTEXT)
        private_key = serialization.load_pem_private_key(private_key_pem, password=None)
        if not isinstance(private_key, ec.EllipticCurvePrivateKey):
            raise ValueError("internal CA private key type is unsupported")
        constraints = certificate.extensions.get_extension_for_class(x509.BasicConstraints).value
        if not constraints.ca:
            raise ValueError("internal CA certificate is not a CA")
        if _public_key_bytes(certificate.public_key()) != _public_key_bytes(private_key.public_key()):
            raise ValueError("internal CA certificate and private key do not match")
        return certificate, private_key


def _build_root_certificate(
    *,
    private_key: ec.EllipticCurvePrivateKey,
    common_name: str,
    valid_days: int,
    now: datetime,
) -> x509.Certificate:
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "AgentBridge"),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ]
    )
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=valid_days))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(private_key.public_key()), critical=False)
        .sign(private_key, hashes.SHA256())
    )


def _build_server_certificate(
    *,
    root_certificate: x509.Certificate,
    root_private_key: ec.EllipticCurvePrivateKey,
    server_private_key: ec.EllipticCurvePrivateKey,
    server_ip: ipaddress.IPv4Address,
    valid_days: int,
    now: datetime,
) -> x509.Certificate:
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "AgentBridge"),
            x509.NameAttribute(NameOID.COMMON_NAME, str(server_ip)),
        ]
    )
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(root_certificate.subject)
        .public_key(server_private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=valid_days))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(server_ip)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=True,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(server_private_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(root_private_key.public_key()),
            critical=False,
        )
        .sign(root_private_key, hashes.SHA256())
    )


def _utc_now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("certificate time must be timezone-aware")
    return value.astimezone(timezone.utc)


def _certificate_fingerprint(certificate: x509.Certificate) -> str:
    return certificate.fingerprint(hashes.SHA256()).hex().upper()


def _public_key_bytes(public_key: object) -> bytes:
    return public_key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _atomic_write(path: Path, data: bytes, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
