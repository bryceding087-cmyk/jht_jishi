"""Video-specific derivatives: bounded payloads, original and reference integrity."""
import base64
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
import video_reference_images as module


def encoded(image, fmt='PNG', **kwargs):
    output=io.BytesIO();image.save(output, fmt, **kwargs);return output.getvalue()


class VideoReferenceImageTests(unittest.TestCase):
    def test_small_image_is_byte_identical(self):
        data=encoded(Image.new('RGB',(256,192),'blue'))
        result=module.encode_reference_image(data)
        self.assertEqual(result.data,data)
        self.assertEqual(result.mime,'image/png')

    def test_large_photo_becomes_small_correctly_typed_derivative(self):
        data=encoded(Image.frombytes('RGB',(2560,1440),os.urandom(2560*1440*3)))
        result=module.encode_reference_image(data)
        self.assertLessEqual(len(result.data),module.TARGET_BYTES)
        self.assertLess(len(result.data),len(data)/5)
        with Image.open(io.BytesIO(result.data)) as image:
            self.assertLessEqual(max(image.size),1920)
            self.assertAlmostEqual(image.width/image.height,2560/1440,delta=.01)
            self.assertEqual(Image.MIME[image.format],result.mime)

    def test_transparency_and_exif_orientation_are_preserved(self):
        transparent=module.encode_reference_image(encoded(Image.new('RGBA',(2400,1200),(255,0,0,70))))
        with Image.open(io.BytesIO(transparent.data)) as image:
            self.assertEqual(image.size,(1920,960))
            self.assertEqual(image.getpixel((0,0))[3],70)
        image=Image.new('RGB',(1200,800),'green');exif=Image.Exif();exif[274]=6
        rotated=module.encode_reference_image(encoded(image,'JPEG',exif=exif))
        with Image.open(io.BytesIO(rotated.data)) as image:
            self.assertEqual(image.size,(800,1200))
            self.assertNotIn(image.getexif().get(274),range(2,9))

    def test_cache_reuses_derivative_without_changing_original_and_invalidates_on_change(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'source.png';original=encoded(Image.new('RGB',(2400,1600),'red'));path.write_bytes(original)
            with patch.object(module,'encode_reference_image',wraps=module.encode_reference_image) as convert:
                first=module.video_reference_image(path);second=module.video_reference_image(path)
                self.assertIs(first,second);self.assertEqual(convert.call_count,1)
                self.assertEqual(path.read_bytes(),original)
                path.write_bytes(encoded(Image.new('RGB',(1200,800),'blue')))
                third=module.video_reference_image(path)
                self.assertEqual(convert.call_count,2);self.assertNotEqual(third.data,first.data)

    def test_corrupt_and_oversized_images_are_rejected(self):
        with self.assertRaises(Exception):module.encode_reference_image(b'not an image')
        with self.assertRaisesRegex(ValueError,'20 MB'):module.encode_reference_image(b'x'*(module.MAX_SOURCE_BYTES+1))


if __name__=='__main__':unittest.main()
