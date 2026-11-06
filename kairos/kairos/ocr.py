"""Windows OCR（v2.5）：winsdk → Windows.Media.Ocr，系统自带引擎零成本。
懒加载：winsdk 未安装或无可用语言包时返回明确提示（诚实降级）。"""
from __future__ import annotations


def ocr_available() -> bool:
    try:
        import winrt.windows.media.ocr as ocr  # noqa: F401
        return True
    except Exception:
        try:
            import winsdk.windows.media.ocr as ocr  # noqa: F401  旧投影兜底
            return True
        except Exception:
            return False


def ocr_image(path: str) -> str:
    """识别图片文字，返回纯文本。失败抛异常由调用方转提示。"""
    import asyncio

    async def _run() -> str:
        from winrt.windows.globalization import Language
        from winrt.windows.graphics.imaging import BitmapDecoder
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage import StorageFile

        engine = (OcrEngine.try_create_from_language(Language("zh-Hans"))
                  or OcrEngine.try_create_from_language(Language("en-US"))
                  or OcrEngine.try_create_from_user_profile_languages())
        if engine is None:
            raise RuntimeError("Windows OCR 无可用语言包（设置→时间和语言→语言→添加中文/英文可选功能）")
        file = await StorageFile.get_file_from_path_async(path)
        stream = await file.open_read_async()
        try:
            decoder = await BitmapDecoder.create_async(stream)
            bitmap = await decoder.get_software_bitmap_async()
            result = await engine.recognize_async(bitmap)
            return result.text or ""
        finally:
            try:
                stream.close()
            except Exception:
                pass

    return asyncio.run(_run())


def extract_pdf(path: str) -> str:
    """PDF 抽文本（pypdf）。"""
    from pypdf import PdfReader
    reader = PdfReader(path)
    parts = []
    for i, page in enumerate(reader.pages):
        t = (page.extract_text() or "").strip()
        if t:
            parts.append(f"--- 第{i + 1}页 ---\n{t}")
    return "\n\n".join(parts)


def extract_docx(path: str) -> str:
    """docx 抽文本（zip + XML 正则，不引入 python-docx 重依赖）。"""
    import re
    import zipfile
    out = []
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", errors="replace")
        # 段落切分 + 去标签
        for para in re.split(r"</w:p>", xml):
            texts = re.findall(r"<w:t[^>]*>([^<]*)</w:t>", para)
            line = "".join(texts).strip()
            if line:
                out.append(line)
    return "\n".join(out)
