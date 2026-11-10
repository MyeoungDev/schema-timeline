// 표준 입력의 [[before, after, withIds], ...] 를 templates/diff.js 로 비교해 결과 배열을 JSON 으로 출력한다.
// withIds 가 참이면 attachIds 로 계보 id 도 붙인다.
const path = require('path');
const { schemaDiff, attachIds } = require(path.join(__dirname, '..', 'templates', 'diff.js'));
let input = '';
process.stdin.on('data', c => { input += c; });
process.stdin.on('end', () => {
  const pairs = JSON.parse(input);
  process.stdout.write(JSON.stringify(pairs.map(([b, a, ids]) => ids ? attachIds(schemaDiff(b, a), b, a) : schemaDiff(b, a))));
});
