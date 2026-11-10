"""테스트 출력 언어를 한국어로 고정한다. 각 테스트 파일이 가장 먼저 import 한다."""
import os

os.environ["SCHEMA_TIMELINE_LANG"] = "ko"
