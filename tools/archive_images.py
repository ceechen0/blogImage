#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Iterable, Literal, Optional

from PIL import Image


ImageKind = Literal["raster", "vector"]


@dataclasses.dataclass(frozen=True)
class ImageInfo:
    """
    图片信息（用于归档与清单输出）。
    """

    source_path: str
    relative_path: str
    extension: str
    kind: ImageKind
    file_bytes: int
    sha256: str
    width: Optional[int]
    height: Optional[int]
    has_alpha: Optional[bool]
    aspect_bucket: Optional[str]
    orientation: Optional[str]
    topic: str


def _iter_files(root_dir: Path) -> Iterable[Path]:
    """
    遍历目录下所有文件（按路径排序，保证输出稳定）。
    """

    for file_path in sorted(root_dir.rglob("*")):
        if file_path.is_file():
            yield file_path


def _sha256_of_file(file_path: Path) -> str:
    """
    计算文件 sha256（用于去重与映射）。
    """

    digest = hashlib.sha256()
    with file_path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_slug(value: str) -> str:
    """
    将字符串转成适合路径的 slug，尽量保留中文与常见符号，移除危险字符。
    """

    value = value.strip().replace(os.sep, "-")
    value = re.sub(r"[\\/:*?\"<>|]+", "-", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value or "unknown"


def _topic_from_path(relative_path: str) -> str:
    """
    根据路径/文件名推断主题分类（可按实际仓库命名逐步补充规则）。
    """

    lower_name = relative_path.lower()
    if "node" in lower_name or "nvm" in lower_name:
        return "node"
    if lower_name.startswith("py-") or "python" in lower_name:
        return "python"
    if "skill" in lower_name:
        return "skill"
    if "证书模板管理" in relative_path or "tc_" in lower_name:
        return "certificate-template"
    if "组件" in relative_path:
        return "component"
    return "misc"


def _aspect_bucket(width: int, height: int) -> tuple[str, str]:
    """
    计算宽高比/方向分桶，便于归档检索。
    """

    if width == 0 or height == 0:
        return "unknown", "unknown"

    ratio = width / height
    if ratio >= 1.6:
        bucket = "wide"
    elif ratio <= 0.625:
        bucket = "tall"
    else:
        bucket = "squareish"

    if width > height:
        orientation = "landscape"
    elif height > width:
        orientation = "portrait"
    else:
        orientation = "square"

    return bucket, orientation


def _inspect_raster(image_path: Path) -> tuple[int, int, bool]:
    """
    读取图片尺寸与 alpha 信息（仅栅格图）。
    """

    with Image.open(image_path) as image:
        width, height = image.size
        has_alpha = ("A" in image.getbands()) or (image.mode in {"LA", "RGBA", "PA"})
        return width, height, has_alpha


def _build_image_info(images_root: Path, image_path: Path) -> ImageInfo:
    """
    构建图片信息结构。
    """

    relative_path = str(image_path.relative_to(images_root)).replace("\\", "/")
    extension = image_path.suffix.lower().lstrip(".")
    file_bytes = image_path.stat().st_size
    sha256 = _sha256_of_file(image_path)

    if extension == "svg":
        kind: ImageKind = "vector"
        width = None
        height = None
        has_alpha = None
        aspect_bucket = None
        orientation = None
    else:
        kind = "raster"
        width, height, has_alpha = _inspect_raster(image_path)
        aspect_bucket, orientation = _aspect_bucket(width, height)

    topic = _topic_from_path(relative_path)
    return ImageInfo(
        source_path=str(image_path),
        relative_path=relative_path,
        extension=extension,
        kind=kind,
        file_bytes=file_bytes,
        sha256=sha256,
        width=width,
        height=height,
        has_alpha=has_alpha,
        aspect_bucket=aspect_bucket,
        orientation=orientation,
        topic=topic,
    )


def _archive_destination(
    *,
    archive_root: Path,
    image_info: ImageInfo,
    keep_original_name: bool,
) -> Path:
    """
    根据图片内容与主题生成归档目标路径。
    """

    topic_dir = _safe_slug(image_info.topic)
    kind_dir = image_info.kind

    if image_info.kind == "vector":
        size_dir = "svg"
    else:
        width = image_info.width or 0
        height = image_info.height or 0
        size_dir = f"{width}x{height}"

    aspect_dir = image_info.aspect_bucket or "unknown"
    orientation_dir = image_info.orientation or "unknown"
    alpha_dir = "alpha" if image_info.has_alpha else "opaque"
    if image_info.has_alpha is None:
        alpha_dir = "unknown"

    if keep_original_name:
        file_name = Path(image_info.relative_path).name
    else:
        file_name = f"{image_info.sha256[:16]}.{image_info.extension}"

    return (
        archive_root
        / topic_dir
        / kind_dir
        / aspect_dir
        / orientation_dir
        / alpha_dir
        / size_dir
        / file_name
    )


def _write_json(file_path: Path, data: Any) -> None:
    """
    写入 JSON（UTF-8，保证中文可读）。
    """

    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="保持旧地址不动，新增归档目录并按图片内容归档。")
    parser.add_argument("--images-root", default="images", help="原始图片目录（默认：images）")
    parser.add_argument(
        "--archive-root",
        default="images_archive",
        help="新增归档目录（默认：images_archive，不会修改 images 下文件）",
    )
    parser.add_argument(
        "--manifest",
        default="images_archive/manifest.json",
        help="归档映射清单输出路径（默认：images_archive/manifest.json）",
    )
    parser.add_argument(
        "--keep-original-name",
        action="store_true",
        help="归档文件保留原文件名（默认使用 sha 前缀命名，避免重名）",
    )
    parser.add_argument(
        "--copy-mode",
        choices=["copy2", "copy"],
        default="copy2",
        help="复制模式：copy2（保留元信息）/ copy（纯复制）",
    )
    args = parser.parse_args()

    workspace_root = Path.cwd()
    images_root = (workspace_root / args.images_root).resolve()
    archive_root = (workspace_root / args.archive_root).resolve()
    manifest_path = (workspace_root / args.manifest).resolve()

    if not images_root.exists():
        raise SystemExit(f"images root not found: {images_root}")

    allowed_extensions = {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".svg",
        ".avif",
        ".ico",
        ".bmp",
        ".tiff",
        ".apng",
    }

    image_files = [file_path for file_path in _iter_files(images_root) if file_path.suffix.lower() in allowed_extensions]
    image_infos: list[ImageInfo] = []
    errors: list[dict[str, str]] = []

    for image_path in image_files:
        try:
            image_infos.append(_build_image_info(images_root, image_path))
        except Exception as exc:  # noqa: BLE001
            errors.append({"path": str(image_path), "error": repr(exc)})

    copy_function = shutil.copy2 if args.copy_mode == "copy2" else shutil.copy

    archived_items: list[dict[str, Any]] = []
    sha_to_archived_path: dict[str, str] = {}

    for info in image_infos:
        destination_path = _archive_destination(
            archive_root=archive_root,
            image_info=info,
            keep_original_name=args.keep_original_name,
        )
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        copy_function(Path(info.source_path), destination_path)

        archived_relative = str(destination_path.relative_to(workspace_root)).replace("\\", "/")
        sha_to_archived_path.setdefault(info.sha256, archived_relative)

        archived_items.append(
            {
                "source": f"{args.images_root}/{info.relative_path}",
                "archived": archived_relative,
                "sha256": info.sha256,
                "bytes": info.file_bytes,
                "kind": info.kind,
                "extension": info.extension,
                "width": info.width,
                "height": info.height,
                "has_alpha": info.has_alpha,
                "aspect_bucket": info.aspect_bucket,
                "orientation": info.orientation,
                "topic": info.topic,
            }
        )

    summary = {
        "generated_at": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
        "images_root": str(Path(args.images_root)),
        "archive_root": str(Path(args.archive_root)),
        "total_images": len(image_infos),
        "total_files_scanned": len(image_files),
        "total_errors": len(errors),
        "errors": errors,
        "items": archived_items,
        "dedupe": {
            "unique_sha256": len(sha_to_archived_path),
            "sha256_to_archived_path": sha_to_archived_path,
        },
    }

    _write_json(manifest_path, summary)

    print(f"Scanned: {len(image_files)} files, archived: {len(image_infos)} images")
    print(f"Archive root: {archive_root}")
    print(f"Manifest: {manifest_path}")
    if errors:
        print(f"Errors: {len(errors)} (see manifest)")


if __name__ == "__main__":
    main()

