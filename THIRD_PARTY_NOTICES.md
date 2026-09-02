# Third-party notices

CopyDesk includes code adapted from the project below. The notice applies to the adapted code in `lib/linter.py`.

## AminBlg/SimpleEnglish

Source: [`evals/ste_lint.py`](https://github.com/AminBlg/SimpleEnglish/blob/59bf6702197a5aadc96d197ea17f290d8d50dcd3/evals/ste_lint.py) at commit `59bf6702197a5aadc96d197ea17f290d8d50dcd3`.

The adapted parts are the whitespace tokenizer, sentence splitter, exclusion approach, and line-oriented reporting. CopyDesk replaces the upstream ASD-STE100 rule list and does not retain its bans on contractions, modal verbs, or semicolons.

MIT License, Copyright (c) 2026 AminBlg.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
