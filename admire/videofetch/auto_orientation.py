from melanie import get_filename_from_url
from runtimeopt import offloaded


@offloaded
def maybe_correct_orientation(url: str) -> tuple:
    from wand.image import Image

    from melanie.curl import worker_download

    data = worker_download(url)
    name = get_filename_from_url(url)
    with Image(blob=data) as img:
        img: Image
        h1 = img.height
        img.auto_orient()
        if img.height != h1:
            return name, img.make_blob(img.format)
