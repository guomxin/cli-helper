from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import ExtendedKeyUsageOID

from bscli.core.internal_pki import InternalCertificateAuthorityStore


class PrefixProtector:
    def protect(self, plaintext: bytes, *, context: bytes) -> bytes:
        return b"protected:" + context + b":" + plaintext[::-1]

    def unprotect(self, ciphertext: bytes, *, context: bytes) -> bytes:
        prefix = b"protected:" + context + b":"
        if not ciphertext.startswith(prefix):
            raise ValueError("protected value context mismatch")
        return ciphertext[len(prefix) :][::-1]


class InternalPkiTests(unittest.TestCase):
    def test_creates_dpapi_style_root_state_and_ip_server_certificate(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            output = root / "output"
            store = InternalCertificateAuthorityStore(state, PrefixProtector())

            result = store.issue_server_certificate(
                server_ip="10.10.50.213",
                output_dir=output,
                now=datetime(2026, 7, 16, tzinfo=timezone.utc),
            )

            self.assertTrue(result.created_root_ca)
            self.assertTrue((state / "root-ca.crt").is_file())
            protected_key = (state / "root-ca.key.dpapi").read_bytes()
            self.assertNotIn(b"PRIVATE KEY", protected_key)
            self.assertFalse((state / "root-ca.key").exists())
            self.assertEqual(result.server_ip, "10.10.50.213")

            root_certificate = x509.load_pem_x509_certificate(
                (output / "root-ca.crt").read_bytes()
            )
            server_certificate = x509.load_pem_x509_certificate(
                (output / "server.crt").read_bytes()
            )
            server_key = serialization.load_pem_private_key(
                (output / "server.key").read_bytes(),
                password=None,
            )
            self.assertEqual(server_certificate.issuer, root_certificate.subject)
            self.assertEqual(
                server_certificate.extensions.get_extension_for_class(
                    x509.SubjectAlternativeName
                ).value.get_values_for_type(x509.IPAddress)[0].compressed,
                "10.10.50.213",
            )
            self.assertEqual(
                server_certificate.extensions.get_extension_for_class(
                    x509.ExtendedKeyUsage
                ).value,
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            )
            self.assertFalse(
                server_certificate.extensions.get_extension_for_class(
                    x509.BasicConstraints
                ).value.ca
            )
            self.assertEqual(
                server_certificate.public_key().public_bytes(
                    serialization.Encoding.DER,
                    serialization.PublicFormat.SubjectPublicKeyInfo,
                ),
                server_key.public_key().public_bytes(
                    serialization.Encoding.DER,
                    serialization.PublicFormat.SubjectPublicKeyInfo,
                ),
            )

    def test_reuses_root_ca_when_issuing_a_new_leaf(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = InternalCertificateAuthorityStore(root / "state", PrefixProtector())
            first = store.issue_server_certificate(
                server_ip="10.10.50.213",
                output_dir=root / "first",
            )
            second = store.issue_server_certificate(
                server_ip="10.10.50.214",
                output_dir=root / "second",
            )

            self.assertTrue(first.created_root_ca)
            self.assertFalse(second.created_root_ca)
            self.assertEqual(first.root_fingerprint_sha256, second.root_fingerprint_sha256)
            self.assertNotEqual(first.server_fingerprint_sha256, second.server_fingerprint_sha256)

    def test_refuses_partial_root_state(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            state.mkdir()
            (state / "root-ca.crt").write_text("incomplete", encoding="ascii")
            store = InternalCertificateAuthorityStore(state, PrefixProtector())

            with self.assertRaisesRegex(ValueError, "incomplete"):
                store.issue_server_certificate(
                    server_ip="10.10.50.213",
                    output_dir=root / "output",
                )

    def test_refuses_to_overwrite_server_output_without_force(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = InternalCertificateAuthorityStore(root / "state", PrefixProtector())
            output = root / "output"
            store.issue_server_certificate(server_ip="10.10.50.213", output_dir=output)

            with self.assertRaisesRegex(FileExistsError, "already exists"):
                store.issue_server_certificate(server_ip="10.10.50.213", output_dir=output)

            renewed = store.issue_server_certificate(
                server_ip="10.10.50.213",
                output_dir=output,
                force=True,
            )
            self.assertFalse(renewed.created_root_ca)

    def test_refuses_to_relabel_an_existing_root_ca(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = InternalCertificateAuthorityStore(root / "state", PrefixProtector())
            store.issue_server_certificate(
                server_ip="10.10.50.213",
                output_dir=root / "first",
            )

            with self.assertRaisesRegex(ValueError, "common name"):
                store.issue_server_certificate(
                    server_ip="10.10.50.213",
                    output_dir=root / "second",
                    root_common_name="Unexpected CA",
                )

    def test_rejects_invalid_validity_and_output_location(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            store = InternalCertificateAuthorityStore(state, PrefixProtector())

            with self.assertRaisesRegex(ValueError, "397"):
                store.issue_server_certificate(
                    server_ip="10.10.50.213",
                    output_dir=root / "output",
                    server_valid_days=398,
                )
            with self.assertRaisesRegex(ValueError, "must differ"):
                store.issue_server_certificate(
                    server_ip="10.10.50.213",
                    output_dir=state,
                )


if __name__ == "__main__":
    unittest.main()
