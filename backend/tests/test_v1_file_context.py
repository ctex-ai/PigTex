import unittest

from app.routes.v1_api import (
    V1FileAttachment,
    V1FileChunk,
    _build_file_context_system_prompt,
    _select_file_chunks_for_context,
)


class V1FileContextSelectionTests(unittest.TestCase):
    def test_select_file_chunks_prefers_relevant_vietnamese_chunk(self) -> None:
        attachment = V1FileAttachment(
            id="file-1",
            filename="billing-policy.docx",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            size=4096,
            extracted_text=(
                "Tong quan he thong.\n\n"
                "Khach hang co the thanh toan tra gop trong 12 thang voi phi tre han neu thanh toan cham.\n\n"
                "Lien he ho tro ky thuat khi can tro giup."
            ),
            text_chars=178,
            truncated=False,
            chunks=[
                V1FileChunk(
                    index=1,
                    label="Tong quan",
                    text="Tai lieu tong quan he thong va muc luc.",
                    char_count=38,
                    truncated=False,
                ),
                V1FileChunk(
                    index=2,
                    label="Chinh sach thanh toan",
                    text="Khach hang co the thanh toan tra gop trong 12 thang voi phi tre han neu thanh toan cham.",
                    char_count=88,
                    truncated=False,
                ),
                V1FileChunk(
                    index=3,
                    label="Ho tro ky thuat",
                    text="Lien he ho tro ky thuat khi can tro giup.",
                    char_count=41,
                    truncated=False,
                ),
            ],
        )

        selected = _select_file_chunks_for_context(
            attachment,
            query_text="Trong file noi gi ve thanh toan tra gop?",
            max_chars=2_000,
        )

        self.assertEqual([chunk.index for chunk, _, _ in selected], [2])

        prompt = _build_file_context_system_prompt(
            [attachment],
            query_text="Trong file noi gi ve thanh toan tra gop?",
        )
        self.assertIsNotNone(prompt)
        self.assertIn("[Relevant chunk 2] Chinh sach thanh toan", prompt)
        self.assertNotIn("[Relevant chunk 1] Tong quan", prompt)

    def test_select_file_chunks_falls_back_to_extracted_text_without_chunks(self) -> None:
        attachment = V1FileAttachment(
            id="file-2",
            filename="notes.txt",
            mime_type="text/plain",
            size=128,
            extracted_text="Deployment checklist and rollback notes.",
            text_chars=38,
            truncated=False,
            chunks=None,
        )

        selected = _select_file_chunks_for_context(
            attachment,
            query_text="",
            max_chars=200,
        )

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0][0].index, 1)
        self.assertEqual(selected[0][1], "Deployment checklist and rollback notes.")

    def test_file_context_prompt_requests_exact_numeric_values_for_price_queries(self) -> None:
        attachment = V1FileAttachment(
            id="file-3",
            filename="pricing.pdf",
            mime_type="application/pdf",
            size=2048,
            extracted_text="Goi Pro co gia 499.000 VND/thang. Goi Enterprise lien he bao gia.",
            text_chars=69,
            truncated=False,
            chunks=[
                V1FileChunk(
                    index=1,
                    label="Bang gia",
                    text="Goi Pro co gia 499.000 VND/thang. Goi Enterprise lien he bao gia.",
                    char_count=69,
                    truncated=False,
                ),
            ],
        )

        prompt = _build_file_context_system_prompt(
            [attachment],
            query_text="Gia goi Pro trong file la bao nhieu?",
        )

        self.assertIsNotNone(prompt)
        self.assertIn("quote the exact number", prompt)
        self.assertIn("filename plus chunk label", prompt)


if __name__ == "__main__":
    unittest.main()
