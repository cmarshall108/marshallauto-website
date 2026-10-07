"""One-shot HEIC decoder: no database or web-app bootstrap."""
import json
import sys

from flask import Flask
from werkzeug.datastructures import FileStorage

from app.utils import _save_uploaded_image_in_process


def main():
    source_path, config_json, width = sys.argv[1:]
    settings = json.loads(config_json)
    if sys.platform.startswith('linux'):
        import resource
        limit = int(settings['HEIC_CONVERSION_MEMORY_MB']) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    app = Flask(__name__)
    app.config.update(settings)
    with app.app_context(), open(source_path, 'rb') as source:
        filename, image_width, image_height = _save_uploaded_image_in_process(
            FileStorage(stream=source, filename='input.heic'),
            subfolder='converted', width=int(width),
        )
        if not filename:
            print('HEIC decoding failed. Convert the photo to JPEG and upload again.', file=sys.stderr)
            return 1
        print(json.dumps({'filename': filename, 'width': image_width, 'height': image_height}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
