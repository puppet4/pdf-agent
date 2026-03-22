from pdf_agent.api.agent import _content_disposition_headers as agent_content_disposition_headers
from pdf_agent.api.files import _content_disposition_headers as file_content_disposition_headers


def test_content_disposition_headers_support_utf8_filenames():
    filename = "GA算法说明_已合并.pdf"

    for helper in (agent_content_disposition_headers, file_content_disposition_headers):
        headers = helper(filename, inline=True)
        content_disposition = headers["Content-Disposition"]

        assert content_disposition.startswith('inline; filename="GA_.pdf";')
        assert "filename*=UTF-8''GA%E7%AE%97%E6%B3%95%E8%AF%B4%E6%98%8E_%E5%B7%B2%E5%90%88%E5%B9%B6.pdf" in content_disposition
        assert "\n" not in content_disposition
        assert "\r" not in content_disposition


def test_content_disposition_headers_fallback_when_name_is_non_ascii_only():
    filename = "测试文档"

    headers = agent_content_disposition_headers(filename, inline=False)
    content_disposition = headers["Content-Disposition"]

    assert content_disposition.startswith('attachment; filename="download";')
    assert "filename*=UTF-8''%E6%B5%8B%E8%AF%95%E6%96%87%E6%A1%A3" in content_disposition
