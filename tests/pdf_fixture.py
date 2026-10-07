"""Small generated text-layer PDFs, including Korean, without proprietary fonts."""
from io import BytesIO

from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject, TextStringObject


def pdf_bytes(texts: list[str], *, encrypted: bool = False) -> bytes:
    writer = PdfWriter()
    chars = sorted(set("".join(texts)))
    codes = {char: index + 1 for index, char in enumerate(chars)}
    cmap = DecodedStreamObject()
    entries = "\n".join(f"<{codes[c]:04x}> <{c.encode('utf-16-be').hex()}>" for c in chars)
    cmap.set_data(("/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n"
                   "/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> def\n"
                   "/CMapName /TestUnicode def /CMapType 2 def\n"
                   "1 begincodespacerange <0000> <FFFF> endcodespacerange\n"
                   f"{len(chars)} beginbfchar\n{entries}\nendbfchar\nendcmap end end").encode("ascii"))
    descendant = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/CIDFontType2"), NameObject("/BaseFont"): NameObject("/TestUnicode"),
        NameObject("/CIDSystemInfo"): DictionaryObject({NameObject("/Registry"): TextStringObject("Adobe"),
            NameObject("/Ordering"): TextStringObject("Identity"), NameObject("/Supplement"): NumberObject(0)}),
        NameObject("/DW"): NumberObject(1000)})
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type0"),
        NameObject("/BaseFont"): NameObject("/TestUnicode"), NameObject("/Encoding"): NameObject("/Identity-H"),
        NameObject("/DescendantFonts"): ArrayObject([writer._add_object(descendant)]),
        NameObject("/ToUnicode"): writer._add_object(cmap)})
    font_ref = writer._add_object(font)
    for text in texts:
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})})
        stream = DecodedStreamObject()
        encoded = "".join(f"{codes[char]:04x}" for char in text)
        stream.set_data(f"BT /F1 12 Tf 40 700 Td <{encoded}> Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    if encrypted:
        writer.encrypt("test-password")
    output = BytesIO()
    writer.write(output)
    return output.getvalue()
