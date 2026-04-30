"""Tests for core utilities — soft delete, branch scoping, file validators."""

from django.test import TestCase
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from .models import validate_file_size, validate_file_extension


class FileValidatorTest(TestCase):
    """Test file upload validators."""

    def test_valid_extension_passes(self):
        f = SimpleUploadedFile('doc.pdf', b'content')
        validate_file_extension(f)  # should not raise

    def test_invalid_extension_rejected(self):
        f = SimpleUploadedFile('script.exe', b'content')
        with self.assertRaises(ValidationError):
            validate_file_extension(f)

    def test_file_too_large_rejected(self):
        # Create a file just over 10MB
        f = SimpleUploadedFile('big.pdf', b'x' * (10 * 1024 * 1024 + 1))
        with self.assertRaises(ValidationError):
            validate_file_size(f)

    def test_file_under_limit_passes(self):
        f = SimpleUploadedFile('small.pdf', b'x' * 1000)
        validate_file_size(f)  # should not raise
