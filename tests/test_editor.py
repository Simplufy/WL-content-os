import numpy as np

from studio.editor import captions, decide, plan


def _words(spec):
    """spec: list of (word, start, end)"""
    return [{"word": w, "start": s, "end": e} for w, s, e in spec]


def _env(duration, speech):
    """-60 dB floor, -20 dB where speech (list of (s, e))"""
    n = int(duration / plan.ENV_HOP)
    env = np.full(n, -60.0)
    for s, e in speech:
        env[int(s / plan.ENV_HOP): int(e / plan.ENV_HOP)] = -20.0
    return env


WORDS = _words([
    ("the", 1.0, 1.2), ("color", 1.25, 1.6), ("tells", 1.65, 1.9),        # 0-2 false start
    ("The", 2.5, 2.7), ("color", 2.75, 3.1), ("tells", 3.15, 3.4), ("you", 3.45, 3.6),  # 3-6
    ("how", 4.5, 4.7), ("much", 4.75, 5.0), ("to", 5.05, 5.1), ("worry.", 5.15, 5.6),   # 7-10 after 0.9s pause
])
ENV = _env(8, [(w["start"], w["end"]) for w in WORDS])


def test_timeline_cuts_false_start_and_shortens_pause():
    segs = plan.build_timeline(WORDS, [(3, 10)], ENV, duration=8)
    assert len(segs) == 2                                  # split at the 0.9s pause
    assert segs[0].words == [3, 4, 5, 6] and segs[1].words == [7, 8, 9, 10]
    assert segs[0].src_in >= WORDS[2]["end"]              # never reaches into the cut false start
    assert segs[0].src_in <= WORDS[3]["start"]
    assert segs[1].src_out >= WORDS[10]["end"]
    for s in segs:                                         # frame aligned
        assert abs(s.src_in * plan.FPS - round(s.src_in * plan.FPS)) < 1e-6
    assert plan.total_duration(segs) < 5.6 - 2.5           # shorter than the raw stretch


def test_output_word_times_are_contiguous():
    segs = plan.build_timeline(WORDS, [(3, 10)], ENV, duration=8)
    ow = plan.output_word_times(WORDS, segs)
    assert [w["i"] for w in ow] == list(range(3, 11))
    assert all(b["start"] >= a["start"] for a, b in zip(ow, ow[1:]))
    assert ow[-1]["end"] <= plan.total_duration(segs) + 1e-6


def test_assign_zoom_alternates_and_punches():
    segs = [plan.Segment(0, 1, words=[0]), plan.Segment(1, 2, words=[1]), plan.Segment(2, 3, words=[2])]
    plan.assign_zoom(segs, {2}, base=1.0, alt=1.1, punch=1.3)
    assert [s.zoom for s in segs] == [1.0, 1.1, 1.3]


def test_sanitize_resolves_quoted_anchors_in_kept_words_only():
    d = decide.sanitize({"keep": [{"from": 5, "to": 3}, {"from": 8, "to": 99}],
                         "emphasis": ["color tells you", "how much", "nonsense phrase here"],
                         "callouts": [{"anchor": "much to worry", "text": "Worry", "color": "amber"},
                                      {"anchor": "the color tells", "text": "X", "color": "red"}]}, 11, WORDS)
    assert d["keep"] == [{"from": 3, "to": 5}, {"from": 8, "to": 10}]
    assert d["emphasis_words"] == [4]           # "how much": word 7 was cut -> fuzzy hit elsewhere or none
    assert [(c["at_word"], c["until_word"]) for c in d["callout_words"]] == [(8, 10), (3, 5)]


def test_find_anchor_tolerates_punctuation_and_case():
    order = list(range(len(WORDS)))
    assert decide.find_anchor(WORDS, order, "How much to WORRY") == (7, 10)
    assert decide.find_anchor(WORDS, order, "completely unrelated words") is None


def test_format_words_marks_pauses():
    txt = decide.format_words(WORDS)
    assert "3:The" in txt and "after 0.9s pause" in txt


def test_ass_has_captions_callouts_hook():
    ow = [{"i": i, "word": w, "start": i * 0.4, "end": i * 0.4 + 0.3} for i, w in enumerate("red means stop right now, amber is fine".split())]
    placed = captions.place_callouts([{"at_word": 0, "until_word": 2, "text": "Red = stop", "color": "red"},
                                      {"at_word": 1, "until_word": 1, "text": "overlap", "color": "white"}], ow)
    assert len(placed) == 1
    ass = captions.build_ass(ow, callouts=placed, hook_text="Dash lights 101", duration=4)
    assert ass.count("Style: Cap") == 1
    assert "RED = STOP" in ass and "DASH LIGHTS 101" in ass
    assert ass.count(",Cap,,") == len(ow)
    groups = captions.group_words(ow)
    assert all(len(g) <= 3 for g in groups)


from studio.editor import qa


def _got(spec):
    return [{"word": w, "start": s, "end": e} for w, s, e in spec]


def test_find_leftovers_catches_inserted_retake_and_stutter():
    intended = "a red oil can means the engine is not getting oil but get it checked".split()
    got = _got([("a", 0.0, 0.1), ("red", 0.1, 0.3), ("oil", 0.3, 0.5), ("can", 0.5, 0.7),
                ("means", 0.7, 0.9), ("that", 1.0, 1.1), ("that", 1.1, 1.2), ("that", 1.2, 1.3),   # junk
                ("the", 1.4, 1.5), ("engine", 1.5, 1.8), ("is", 1.8, 1.9), ("not", 1.9, 2.0),
                ("getting", 2.0, 2.3), ("oil", 2.3, 2.5), ("but", 2.6, 2.7), ("but", 2.7, 2.8),  # stutter
                ("get", 2.9, 3.0), ("it", 3.0, 3.1), ("checked", 3.1, 3.5)])
    r = qa.find_leftovers(intended, got)
    assert len(r) == 2
    assert r[0][0] >= 0.9 and r[0][1] <= 1.4          # between "means" and "the"
    assert 2.5 <= r[1][0] and r[1][1] <= 2.9


def test_clean_cut_has_no_leftovers():
    intended = "every light on your dash".split()
    got = _got([(w, k * 0.3, k * 0.3 + 0.25) for k, w in enumerate(["Every", "light", "on", "your", "dash."])])
    assert qa.find_leftovers(intended, got) == []


def test_out_to_src_and_exclusions_split_segments():
    words = [{"word": f"w{i}", "start": 10 + i * 0.5, "end": 10 + i * 0.5 + 0.4} for i in range(6)]
    segs = [plan.Segment(10.0, 11.5, words=[0, 1, 2]), plan.Segment(20.0, 21.0, words=[3, 4])]
    # output 1.0-1.2 is inside seg0 (src 11.0-11.2); output 1.6-1.8 is inside seg1 (src 20.1-20.3)
    src = qa.out_to_src([(1.0, 1.2), (1.6, 1.8)], segs)
    assert [(round(a, 2), round(b, 2)) for a, b in src] == [(11.0, 11.2), (20.1, 20.3)]
    new = qa.apply_exclusions(segs, words, [(11.0, 11.2)])
    assert [(s.src_in, s.src_out) for s in new][:2] == [(10.0, 11.0), (11.2, 11.5)]


def test_compress_silences_cuts_hidden_pause_inside_a_word_run():
    words = _words([("so", 0.0, 0.3), ("anything", 0.35, 1.6), ("red", 1.65, 1.9)])  # 'anything' stretched over a pause
    env = _env(3, [(0.0, 0.3), (0.35, 0.8), (1.4, 1.9)])                           # real silence 0.8-1.4
    segs = [plan.Segment(0.0, 2.0, words=[0, 1, 2])]
    out = plan.compress_silences(segs, env, words)
    assert len(out) == 2
    assert plan.total_duration(out) < 2.0 - 0.35
    gap_kept = (out[0].src_out - 0.8) + (1.4 - out[1].src_in)
    assert 0.1 <= gap_kept <= 0.25


def test_compress_silences_trims_edge_silence():
    words = _words([("anything", 1.0, 1.6), ("red", 1.65, 1.9)])
    env = _env(3, [(1.5, 1.9)])                      # whisper says speech starts at 1.0; audio says 1.5
    out = plan.compress_silences([plan.Segment(0.9, 2.4, words=[0, 1])], env, words)
    assert len(out) == 1
    assert 1.38 <= out[0].src_in <= 1.47              # pulled up to ~80ms before real onset
    assert out[0].src_out <= 2.1                       # trailing silence trimmed too


from studio.editor import sfx


def test_sfx_track_is_quiet_and_timed():
    gfx = [{"type": "checklist", "start": 1.0, "end": 4.0, "items": ["a", "b", "c"]},
           {"type": "word_slam", "start": 6.0, "end": 7.5}]
    cues = sfx.cues_for(gfx)
    assert any(v == "tick" for _, v, _ in cues) and any(v == "thump" for _, v, _ in cues)
    track = sfx.render_track(cues, 8.0)
    assert len(track) == 8 * sfx.SR
    peak_db = 20 * np.log10(np.max(np.abs(track)))
    assert -23 < peak_db < -21                       # sits well under -14 LUFS speech
    assert np.max(np.abs(track[: int(0.9 * sfx.SR)])) == 0   # nothing before the first graphic
    voice = np.full(8 * sfx.SR, 0.95, dtype=np.float32)
    assert np.max(np.abs(sfx.mix_into(voice, track))) <= 10 ** (-1 / 20) + 1e-6


def test_find_leftovers_catches_half_words_and_dashes():
    intended = "blue or green is just info headlights high beams the red thermometer".split()
    seq = [("Blue", 0.0, 0.2), ("or", 0.2, 0.4), ("-", 0.4, 0.4), ("blue", 0.5, 0.7), ("or", 0.7, 0.8), ("green", 0.8, 1.0),
           ("is", 1.0, 1.1), ("just", 1.1, 1.3), ("info.", 1.3, 1.6), ("Head", 1.9, 2.1), ("-", 2.1, 2.1),
           ("headlights,", 2.4, 2.9), ("hi", 3.0, 3.1), ("-", 3.1, 3.1), ("high", 3.1, 3.3), ("beams,", 3.3, 3.6),
           ("A", 4.0, 4.1), ("ther", 4.2, 4.3), ("-", 4.3, 4.3), ("the", 4.5, 4.6), ("red", 4.8, 5.0), ("thermometer", 5.0, 5.5)]
    r = qa.find_leftovers(intended, _got(seq))
    covered = lambda t: any(a <= t <= b for a, b in r)
    assert covered(0.1) or covered(0.3)          # first "blue or"
    assert covered(2.0)                          # "Head-"
    assert covered(3.05)                         # "hi-"
    assert covered(4.25)                         # "ther-"
    for keep in (0.9, 1.4, 2.6, 3.2, 5.2):       # real words survive
        assert not covered(keep), keep


def test_single_real_word_insert_is_kept():
    intended = "red means stop".split()
    got = _got([("red", 0, .2), ("light", .2, .5), ("means", .5, .7), ("stop", .7, 1.0)])
    assert qa.find_leftovers(intended, got) == []


def test_remove_overlaps():
    segs = [plan.Segment(120.97, 122.97, words=[1]), plan.Segment(122.63, 123.87, words=[2]), plan.Segment(10.0, 11.0, words=[0])]
    out = plan.remove_overlaps(segs)
    assert out[1].src_in == plan.frame(122.97)
    assert out[2].src_in == 10.0                  # a deliberate reorder (cold open) is untouched


def test_repeat_removes_earlier_copy():
    g = _got([("info", 0, .3), ("blue", .5, .7), ("or", .7, .8), ("-", .8, .8), ("blue", 1.0, 1.2), ("or", 1.2, 1.3), ("green", 1.3, 1.6)])
    r = qa.find_leftovers("info blue or green".split(), g)
    assert len(r) == 1 and r[0][0] < 0.55 and r[0][1] <= 1.0


def test_unheard_island_found_between_words_but_not_word_edges():
    hop = plan.ENV_HOP
    env = np.full(400, -60.0)
    env[0:120] = -20      # "damage" 0-1.2
    env[150:175] = -22    # skipped false start 1.5-1.75
    env[200:260] = -20    # "the red" 2.0-2.6
    got = _got([("damage.", 0.0, 1.2), ("The", 2.0, 2.3), ("red", 2.3, 2.6)])
    isl = qa.unheard_islands(got, env, hop)
    assert len(isl) == 1 and 1.4 < isl[0][0] < 1.55 and 1.7 < isl[0][1] < 1.85
    got_late = _got([("damage.", 0.0, 1.2), ("The", 2.1, 2.3), ("red", 2.3, 2.6)])  # word timed late: onset not cut
    assert all(b < 2.0 for _, b in qa.unheard_islands(got_late, env, hop))
