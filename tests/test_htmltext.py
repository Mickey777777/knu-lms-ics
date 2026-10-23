from knu_lms_ics.htmltext import contains_evidence, html_to_text


def test_html_to_text_keeps_blocks_and_decodes_entities():
    html = "<p>중간고사&nbsp;안내</p><div>일시: 10/28(수)<br>15:00~16:15</div><script>alert(1)</script><ul><li>범위 1~5장</li></ul>"
    assert html_to_text(html) == "중간고사 안내\n\n일시: 10/28(수)\n15:00~16:15\n\n범위 1~5장"


def test_html_to_text_collapses_blank_lines():
    assert html_to_text("<p>a</p><p></p><p></p><p>b</p>") == "a\n\nb"


def test_contains_evidence_ignores_whitespace_and_width():
    source = "시험은 10월 28일\n15:00에 공학관 101호에서 진행합니다."
    assert contains_evidence(source, "시험은 10월 28일 15:00에 공학관 101호에서 진행합니다.")
    assert contains_evidence(source, "시험은 10월 28일 15：00에")


def test_contains_evidence_rejects_paraphrase_and_empty():
    source = "시험은 10월 28일 15:00에 진행합니다."
    assert not contains_evidence(source, "시험은 10월 28일 오후 3시에 진행합니다.")
    assert not contains_evidence(source, "   ")
