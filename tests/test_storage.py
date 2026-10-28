from __future__ import annotations

from io import BytesIO
import unittest
from unittest.mock import patch

from botocore.exceptions import ClientError

from app.integrations.aws.s3 import read_storage_object, write_storage_object


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.put_requests: list[dict[str, object]] = []

    def put_object(self, **request):
        self.put_requests.append(request)
        identifier = (request["Bucket"], request["Key"])
        if identifier in self.objects and request.get("IfNoneMatch") == "*":
            raise ClientError(
                {
                    "Error": {"Code": "PreconditionFailed"},
                    "ResponseMetadata": {"HTTPStatusCode": 412},
                },
                "PutObject",
            )
        self.objects[identifier] = request["Body"]
        return {}

    def get_object(self, *, Bucket, Key):
        identifier = (Bucket, Key)
        if identifier not in self.objects:
            raise ClientError(
                {
                    "Error": {"Code": "NoSuchKey"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                },
                "GetObject",
            )
        return {"Body": BytesIO(self.objects[identifier])}


class StorageTests(unittest.TestCase):
    def test_requires_s3_bucket(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "Missing MEMORY_S3_BUCKET"):
                write_storage_object("workspace/source.bin", b"memory text")

    def test_rejects_unsafe_object_keys(self) -> None:
        with patch.dict(
            "os.environ", {"MEMORY_S3_BUCKET": "private-memory-sources"}, clear=True
        ):
            with self.assertRaisesRegex(ValueError, "Invalid storage object key"):
                write_storage_object("../outside.bin", b"unsafe")

    def test_s3_backend_writes_and_reads_private_payload(self) -> None:
        client = FakeS3Client()
        with patch.dict(
            "os.environ",
            {
                "MEMORY_S3_BUCKET": "private-memory-sources",
                "MEMORY_S3_PREFIX": "headspace",
            },
            clear=True,
        ), patch("app.integrations.aws.s3.get_s3_client", return_value=client):
            write_storage_object("workspace/source.bin", b"memory text")
            stored = read_storage_object("workspace/source.bin")

        self.assertEqual(stored, b"memory text")
        self.assertEqual(
            client.objects[
                ("private-memory-sources", "headspace/workspace/source.bin")
            ],
            b"memory text",
        )
        request = client.put_requests[0]
        self.assertEqual(request["IfNoneMatch"], "*")
        self.assertNotIn("ACL", request)
        self.assertEqual(
            request["Metadata"]["sha256"],
            "37defeadb01c8149cc1363651410df2c3e96b5456efc3fe155c5ed781ee7db3c",
        )

    def test_s3_backend_preserves_idempotency(self) -> None:
        client = FakeS3Client()
        environment = {"MEMORY_S3_BUCKET": "private-memory-sources"}
        with patch.dict("os.environ", environment, clear=True), patch(
            "app.integrations.aws.s3.get_s3_client", return_value=client
        ):
            write_storage_object("workspace/source.bin", b"first")
            write_storage_object("workspace/source.bin", b"first")
            with self.assertRaisesRegex(ValueError, "different content"):
                write_storage_object("workspace/source.bin", b"second")

    def test_s3_backend_maps_missing_object_to_file_not_found(self) -> None:
        client = FakeS3Client()
        with patch.dict(
            "os.environ",
            {"MEMORY_S3_BUCKET": "private-memory-sources"},
            clear=True,
        ), patch("app.integrations.aws.s3.get_s3_client", return_value=client):
            with self.assertRaises(FileNotFoundError):
                read_storage_object("workspace/missing.bin")


if __name__ == "__main__":
    unittest.main()
