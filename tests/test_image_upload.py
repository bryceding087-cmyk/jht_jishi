"""Image upload validation without reading or changing workspace data."""
import io
import unittest
from unittest.mock import patch, MagicMock

from fastapi import HTTPException
from PIL import Image
from asset_service import validate_bytes


def encoded_image(fmt, mode='RGB'):
    output = io.BytesIO()
    Image.new(mode, (48, 32)).save(output, fmt)
    return output.getvalue()


def encoded_mpo():
    output = io.BytesIO()
    Image.new('RGB', (48, 32), 'red').save(
        output, 'MPO', save_all=True,
        append_images=[Image.new('RGB', (48, 32), 'blue')])
    return output.getvalue()


class ImageUploadTests(unittest.TestCase):
    def test_supported_formats_with_mismatched_extensions(self):
        for fmt in ('JPEG', 'PNG', 'WEBP'):
            for suffix in ('.jpg', '.jpeg', '.png', '.webp'):
                with self.subTest(fmt=fmt, suffix=suffix):
                    data, stored_suffix, _ = validate_bytes(encoded_image(fmt), suffix)
                    self.assertEqual(stored_suffix, '.jpg')
                    with Image.open(io.BytesIO(data)) as image:
                        self.assertEqual(image.format, 'JPEG')
                        self.assertEqual(image.size, (48, 32))
                        image.load()

    def test_mpo_photo_uses_primary_frame_as_standard_jpeg(self):
        photo = encoded_mpo()
        with Image.open(io.BytesIO(photo)) as image:
            self.assertEqual(image.format, 'MPO')
            self.assertEqual(image.n_frames, 2)
        for suffix in ('.jpg', '.jpeg', '.png', '.webp'):
            with self.subTest(suffix=suffix):
                data, stored_suffix, _ = validate_bytes(photo, suffix)
                self.assertEqual(stored_suffix, '.jpg')
                with Image.open(io.BytesIO(data)) as image:
                    self.assertEqual(image.format, 'JPEG')
                    self.assertEqual(image.size, (48, 32))
                    self.assertEqual(getattr(image, 'n_frames', 1), 1)
                    self.assertNotIn('mp', image.info)
                    red, green, blue = image.getpixel((24, 16))
                    self.assertGreater(red, 240)
                    self.assertLess(green, 10)
                    self.assertLess(blue, 10)

    def test_phone_photo_orientation_is_applied(self):
        for fmt in ('JPEG', 'MPO'):
            with self.subTest(fmt=fmt):
                photo = Image.new('RGB', (48, 32), 'red')
                exif = photo.getexif()
                exif[274] = 6  # Phone held in portrait: rotate 90 degrees clockwise.
                output = io.BytesIO()
                options = {'save_all': True, 'append_images': [Image.new('RGB', (48, 32), 'blue')]} if fmt == 'MPO' else {}
                photo.save(output, fmt, exif=exif, **options)
                data, suffix, _ = validate_bytes(output.getvalue(), '.jpg')
                self.assertEqual(suffix, '.jpg')
                with Image.open(io.BytesIO(data)) as image:
                    self.assertEqual(image.size, (32, 48))
                    self.assertIsNone(image.getexif().get(274))

    def test_transparency_survives_wrong_extension(self):
        data, suffix, _ = validate_bytes(encoded_image('PNG', 'RGBA'), '.jpg')
        self.assertEqual(suffix, '.png')
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual(image.format, 'PNG')
            self.assertEqual(image.getpixel((0, 0))[3], 0)

    def test_disguised_unsupported_image_rejected(self):
        with self.assertRaises(HTTPException) as caught:
            validate_bytes(encoded_image('GIF'), '.png')
        self.assertEqual(caught.exception.status_code, 422)
        self.assertIn('实际格式不受支持', caught.exception.detail)

    def test_corrupt_and_non_image_files_rejected(self):
        for data in (b'<svg onload="alert(1)"></svg>', encoded_image('JPEG')[:150], encoded_image('PNG')[:40]):
            with self.subTest(size=len(data)), self.assertRaises(HTTPException) as caught:
                validate_bytes(data, '.png')
            self.assertEqual(caught.exception.status_code, 422)
            self.assertIn('损坏', caught.exception.detail)

    def test_pixel_limit_checked_before_decoding(self):
        image = MagicMock(format='PNG', width=10000, height=4001)
        image.__enter__.return_value = image
        with patch('asset_service.Image.open', return_value=image):
            with self.assertRaises(HTTPException) as caught:
                validate_bytes(b'oversized', '.png')
        self.assertIn('4000 万', caught.exception.detail)
        image.verify.assert_not_called()

    def test_empty_and_byte_limit_remain_enforced(self):
        for data, status in ((b'', 422), (b'x' * (20 * 1024 * 1024 + 1), 413)):
            with self.assertRaises(HTTPException) as caught:
                validate_bytes(data, '.jpg')
            self.assertEqual(caught.exception.status_code, status)


if __name__ == '__main__':
    unittest.main()
