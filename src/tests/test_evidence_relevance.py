from sage.runtime.evidence_relevance import subject_candidate_indices


def test_collection_label_does_not_promote_unrelated_documents():
    assert subject_candidate_indices(
        ("Wiki", "StateAxis"),
        (
            "Wiki | StateAxis limitations",
            "Wiki | prompt tutorial",
            "Wiki | a handbook",
        ),
    ) == (0,)


def test_all_comparison_subjects_and_late_mentions_are_preserved():
    assert subject_candidate_indices(
        ("Library", "Alpha", "Beta"),
        (
            "Library " + "text " * 1000 + "Alpha",
            "Library Beta",
            "Library unrelated",
        ),
    ) == (0, 1)


def test_no_discriminating_match_preserves_semantic_candidates():
    assert subject_candidate_indices(("Wiki", "unknown"), ("Wiki a", "Wiki b")) == (0, 1)
    assert subject_candidate_indices((), ("a", "b")) == (0, 1)
    assert subject_candidate_indices(("Alpha",), ("Alpha only",)) == (0,)


def test_subject_is_literal_and_not_substring():
    assert subject_candidate_indices(("Alpha",), ("Alphanumeric", "Alpha")) == (1,)
    assert subject_candidate_indices(("A+B",), ("AAB", "A+B")) == (1,)


def test_adding_source_cannot_evict_existing_candidates():
    from sage.runtime.evidence_relevance import interleave_source_candidates
    wiki = ['w1', 'w2', 'correct-inference', 'w4', 'w5', 'overflow']
    private = ['p1', 'p2', 'p3', 'p4', 'p5', 'overflow']
    public = interleave_source_candidates([wiki], 5)
    member = interleave_source_candidates([wiki, private], 5)
    assert public == tuple(wiki[:5])
    assert set(public) <= set(member)
    assert member == ('w1','p1','w2','p2','correct-inference','p3','w4','p4','w5','p5')
    assert 'overflow' not in member
