from eval import gmdcsa, videos


def test_parse_classes():
    c = gmdcsa.parse_classes("Falling (SW)[3.4 to 6]; Sitting[0 to 3.4]")
    assert c == [("Falling", 3.4, 6.0), ("Sitting", 0.0, 3.4)]
    assert gmdcsa.parse_classes("Falling (BW)[2.3 6]; Sitting[0 to 2]")[0] == ("Falling", 2.3, 6.0)
    assert gmdcsa.parse_classes("Falling (BW[3 to 5]")[0] == ("Falling", 3.0, 5.0)
    assert gmdcsa.parse_classes("Fall (FW)[0 to 7]")[0][0] == "Fall"


def test_parse_row_commas_and_typo():
    r = gmdcsa.parse_row(
        "03.mp4,05, Day, Shirt, Left fall, not visible, Falling (SW)[2.5 to 5]; Sitting[0 to 2.5]"
    )
    assert r["name"] == "03.mp4"
    assert r["description"] == "Left fall, not visible"
    assert gmdcsa.fall_intervals(r["classes"]) == [(2.5, 5.0)]
    r = gmdcsa.parse_row("15.mp4,04,Day,Shirt,Fall on floor, Falling (FW); Sitting[0 to 1.7]")
    assert gmdcsa.fall_intervals(r["classes"]) == [(1.7, 4.0)]


def test_adl_row_is_negative():
    r = gmdcsa.parse_row(
        "01.mp4,08, Day, Shirt, Sitting to sleeping, Sitting[0 to 1]; Sleeping[2 to 8]"
    )
    assert gmdcsa.fall_intervals(r["classes"]) == []
    assert "проверка ложных падений" in gmdcsa.intervals_text(r["classes"])


def test_catalog_labels_from_csv():
    row = {
        "file": "gmdcsa-s1-fall-01.mp4",
        "intervals": gmdcsa.intervals_text([("Falling", 3.4, 6.0)]),
    }
    assert row["intervals"] == "fall 3.4-6 (CSV авторов набора)"
    lab = videos.labels_from_catalog([row])
    assert lab == [
        {
            "camera": "gmdcsa-s1-fall-01",
            "type": "fall",
            "start_s": 3.4,
            "end_s": 6.0,
            "source": "CSV авторов набора",
        }
    ]
    adl = {"file": "gmdcsa-s1-adl-01.mp4", "intervals": gmdcsa.intervals_text([])}
    assert videos.labels_from_catalog([adl]) == []


def test_sync_replaces_rows():
    md = "a\n| gmdcsa-old.mp4 | x |\n| gmdcsa-old2.mp4 | x |\nb\n"
    rows = [dict.fromkeys(videos.COLUMNS, "n")]
    out = gmdcsa.sync(rows, md)
    assert "gmdcsa-old" not in out and out.count("\n| n |") == 1 and out.endswith("b\n")
