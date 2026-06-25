import urllib.request
import urllib.parse
import json
import time
from datetime import datetime, timedelta, timezone

client_id = ""
client_secret = ""

def fetch_naver_news(query, total=1000, display=100):
    enc_query = urllib.parse.quote(query)
    all_items = []

    for start in range(1, total + 1, display):
        url = (
            f"https://openapi.naver.com/v1/search/news"
            f"?query={enc_query}&display={display}&start={start}&sort=date"
        )
        req = urllib.request.Request(url)
        req.add_header("X-Naver-Client-Id", client_id)
        req.add_header("X-Naver-Client-Secret", client_secret)

        try:
            response = urllib.request.urlopen(req)
            if response.getcode() == 200:
                data = json.loads(response.read().decode('utf-8'))
                items = data.get("items", [])
                all_items.extend(items)
                print(f"[{start}~{start + display - 1}] 수집 완료 → 누적 {len(all_items)}건")

                if len(items) < display:  # 마지막 페이지 도달 시 조기 종료
                    print("더 이상 결과 없음. 종료합니다.")
                    break
            else:
                print(f"Error: HTTP {response.getcode()}")
                break

        except Exception as e:
            print(f"요청 실패 (start={start}): {e}")
            break

        time.sleep(0.1)  # API 호출 간격 (과호출 방지)

    return all_items


# 실행
keyword = ""
items = fetch_naver_news(keyword, total=1000, display=100)

KST = timezone(timedelta(hours=9)) # 한국 표준시 (UTC+9)
now = datetime.now(KST)
# now_str = now.strftime('%Y-%m-%d %H:%M:%S')
now_str = now.strftime('%Y%m%d')

# JSON 저장
output = {"total_collected": len(items), "items": items}
file_name = f"naver_news_{keyword}_{now_str}.json"
with open(file_name, "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f"\n✅ 저장 완료: 총 {len(items)}건 → {file_name}")