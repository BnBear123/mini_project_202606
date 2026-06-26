# Data Collection and Preprocessing
## 뉴스 기사 수집
- 네이버 뉴스 api 사용
- 수집 키워드 : 환율, 국제유가, 유류세, 중동 전쟁
  - 장점 : 키워드 당 1000건 수집 가능
  - 단점 : 키워드 당 1000건만 수집 가능, 일자별로 수집 불가능, 키워드 별 중복 기사 있는지 확인 필요

## 중복 기사 제거 및 뉴스 STM 생성
- STM 에서 LTM 생성할 때 llm 사용
  - llm 에게 넘겨 줄 때 프롬프트 + 스키마 형식이어서 토큰 제한 걸림
  - 그래서 동일 일자별 기사를 2시간 단위로 나눔

## STM to LTM
- gemini api 를 사용해서 생성
  - 무료 등급이다 보니 사용 제한이 걸림
  - 무엇보다 툭하면 503 UNAVAILABLE 가 떠서 생성 불가능할 때가 많음
- chromadb 이해 부족으로 아래 코드 때문에 재적재
- `collection.delete(ids=existing_ids)` 

## STM to LTM to Chromadb
- chromadb 적재까지 하는 코드
  - 보완점 1 : stm 에서 ltm 으로 생성할 때 json 구조가 아닐때가 있어서 retry folder로 이동 parse_gemini_json() 함수를 수정하면 되긴하지만 일단 진행
  - 보완점 2 : 429 또는 503 에러 나는게 ltm 생성 단계인지 임베딩 단계인지 모호함
    - 아마 나중에 다시 ltm 적재해야할지도...
