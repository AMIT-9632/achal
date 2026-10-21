# SPDX-License-Identifier: MIT
from gateway import Buffer


def test_buffer_keeps_order_until_dropped(tmp_path):
    buf = Buffer(str(tmp_path / "b.sqlite"))
    for i in range(3):
        buf.put("t", str(i))
    rows = buf.peek()
    assert [r[2] for r in rows] == ["0", "1", "2"]
    buf.drop([rows[0][0]])
    assert buf.size() == 2


def test_buffer_survives_restart(tmp_path):
    path = str(tmp_path / "b.sqlite")
    Buffer(path).put("t", "x")
    assert Buffer(path).size() == 1
