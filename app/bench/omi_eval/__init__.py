"""Medical STT scoring vendored from github.com/Omi-Health/medical-STT-eval (MIT, see LICENSE)
at commit c0e6c11528ec150f5ff98627f8e86c2d6e731c33. Changes: imports made relative. The
benchmark's word alignment is replaced at call time by an equivalent C implementation
(app/bench/primock.py) for speed; the scoring rules are unchanged."""
