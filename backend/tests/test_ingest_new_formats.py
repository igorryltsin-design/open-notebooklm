from pathlib import Path
from unittest import mock
import sys
import types

if "pptx" not in sys.modules:
    pptx = types.ModuleType("pptx")
    class Presentation:  # pragma: no cover
        pass
    pptx.Presentation = Presentation
    sys.modules["pptx"] = pptx

if "docx" not in sys.modules:
    docx = types.ModuleType("docx")
    class Document:  # pragma: no cover
        pass
    docx.Document = Document
    sys.modules["docx"] = docx

from app.services import ingest_service


def test_parse_file_routes_legacy_office_formats_via_preview_pdf():
    with mock.patch.object(ingest_service, "_parse_via_preview_pdf", return_value="ok") as via_pdf:
        assert ingest_service.parse_file(Path("sample.doc")) == "ok"
        assert ingest_service.parse_file(Path("sample.rtf")) == "ok"
        assert ingest_service.parse_file(Path("sample.odt")) == "ok"
        assert ingest_service.parse_file(Path("sample.otd")) == "ok"
        assert ingest_service.parse_file(Path("sample.ppt")) == "ok"
        assert ingest_service.parse_file(Path("sample.djvu")) == "ok"
        assert ingest_service.parse_file(Path("sample.djv")) == "ok"
        assert ingest_service.parse_file(Path("sample.djvy")) == "ok"
        assert via_pdf.call_count == 8


def test_ensure_preview_pdf_routes_to_expected_converter():
    src_doc = Path("sample.doc")
    src_djvu = Path("sample.djvu")
    with mock.patch.object(ingest_service, "_convert_office_to_pdf", return_value=Path("out.pdf")) as office_conv, \
         mock.patch.object(ingest_service, "_convert_djvu_to_pdf", return_value=Path("out_djvu.pdf")) as djvu_conv:
        assert ingest_service.ensure_preview_pdf(src_doc, document_id="a1") == Path("out.pdf")
        office_conv.assert_called_once_with(src_doc, document_id="a1")
        assert ingest_service.ensure_preview_pdf(src_djvu, document_id="b2") == Path("out_djvu.pdf")
        djvu_conv.assert_called_once_with(src_djvu, document_id="b2")
