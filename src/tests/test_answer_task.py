import pytest

from sage.runtime.answer_task import AnswerTask


@pytest.mark.parametrize("question", [
    "公开资料有没有报告真实研究问题的准确率？给出测量条件。",
    "请总结论文的贡献、实验设计和负结果。",
    "What controlled experiments does the review report?",
    "不要提出研究建议，只查已有数据。",
])
def test_factual_topic_does_not_enable_prospective_work(question):
    assert not AnswerTask("facts").validate(question).research_requested


def test_mixed_task_requires_current_directive():
    question = "先核对现有结果，再为缺口设计对照实验。"
    assert AnswerTask("research", "为缺口设计对照实验").validate(question).research_requested


@pytest.mark.parametrize("task", [AnswerTask("research"),
    AnswerTask("research", "设计不存在的实验"), AnswerTask("facts", "设计实验"),
    AnswerTask("unknown")])
def test_invalid_task_cannot_activate_work(task):
    with pytest.raises(ValueError):
        task.validate("查一下原文。")


def test_direction_request_does_not_authorize_experimental_plan():
    question = "推理状态管理有哪些值得验证的研究问题？"
    task = AnswerTask("research", "有哪些值得验证的研究问题").validate(question)
    assert task.research_requested and not task.experiments_requested
    task = AnswerTask("research", "设计对照实验", "设计对照实验").validate("请设计对照实验。")
    assert task.experiments_requested
    with pytest.raises(ValueError):
        AnswerTask("research", "值得验证", "设计实验").validate(question)
    with pytest.raises(ValueError):
        AnswerTask("facts", None, "设计实验").validate("不要设计实验。")
