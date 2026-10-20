# PyBynix

Bynix-এর জন্য unofficial Python data ও trade execution library। কোনো trading strategy
বা signal নেই। এটি প্রাথমিক alpha; [English README](README.md)-তে installation ও সব
interface আছে। Python 3.11+ প্রয়োজন।

এই checkout থেকে install: `python -m pip install .`

নিজের environment-এ `BYNIX_TOKEN` অথবা `BYNIX_EMAIL` ও `BYNIX_PASSWORD` রাখো।
`pybynix doctor` connection পরীক্ষা করে; `pybynix stream --count 5` live ticks দেখায়।
CLI কোনো trade করে না।

Demo account default। Real trade করতে explicit opt-in লাগে। Order পাঠানোর পর connection
হারালে ফল অনিশ্চিত হতে পারে; একই order আবার না দিয়ে আগে reconcile করতে হবে।
Session/token/password কখনো GitHub-এ দিও না।

Live tick, candle history, account data, session login ও explicit order methods আছে।
কোন feature কতটুকু পরীক্ষা হয়েছে তা [validation](docs/VALIDATION.md)-তে দেখো।
Derived candle sentiment trader-দের actual positioning নয়। Configured domains-এ login
fallback, HTTP CONNECT proxy ও static DNS override আছে। Funded/tournament execution,
SOCKS এবং arbitrary DNS server configuration এখনো এই alpha-তে নেই।
