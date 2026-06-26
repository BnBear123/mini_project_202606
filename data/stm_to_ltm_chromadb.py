import os
from pathlib import Path
from glob import glob
from tqdm import tqdm
import json
from google import genai
import chromadb
from chromadb.config import Settings
import shutil

from memory.ltm import ensure_ltm_vector_collection

# stm -> ltm -> chromadb 적재하는 코드

def find_project_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "output_news" / "test.txt").exists():
            return candidate
    raise FileNotFoundError("Could not find repository root containing output_news/*.json")

PROJECT_ROOT = find_project_root()
os.chdir(PROJECT_ROOT)

CHROMA_STORE_PATH = PROJECT_ROOT / "demo" / "chroma_gemini_handson_2"
PROMOTION_MODEL = "gemini-3.5-flash"
CHATBOT_MODEL = "gemini-3.5-flash"
EMBEDDING_MODEL = "gemini-embedding-001"

GEMINI_API_KEY = "" # my_account3

if not GEMINI_API_KEY:
    raise RuntimeError(".env에 GEMINI_API_KEY 또는 GOOGLE_API_KEY를 설정하세요.")

client = genai.Client(api_key=GEMINI_API_KEY)

CHROMA_STORE_PATH.mkdir(parents=True, exist_ok=True)

CONFIG = {
    "chroma_store_path": CHROMA_STORE_PATH,
    "promotion_model": PROMOTION_MODEL,
    "chatbot_model": CHATBOT_MODEL,
    "gemini_api_key_loaded": bool(GEMINI_API_KEY),
}

# 입력 STM JSON 구조를 검증하고, 실습에서 사용할 대화 개수를 확인합니다.
def validate_stm_payload(payload):
    required_conversation_keys = {"session_id", "articles"}
    required_message_keys = {"id", "memory_type", "session_id", "title", "link", "pubDate", "turn_index", "author", "category"}
    conversations = payload.get("stm_articles")
    if not isinstance(conversations, list) or not conversations:
        raise ValueError("stm_articles는 비어 있지 않은 리스트여야 합니다.")

    message_count = 0
    for conversation_index, conversation in enumerate(conversations):
        missing = required_conversation_keys - conversation.keys()
        if missing:
            raise ValueError(f"conversation[{conversation_index}] 누락 필드: {sorted(missing)}")
        if not isinstance(conversation.get("articles"), list) or not conversation["articles"]:
            raise ValueError(f"conversation[{conversation_index}].articles는 비어 있지 않은 리스트여야 합니다.")
        for message_index, message in enumerate(conversation["articles"]):
            missing = required_message_keys - message.keys()
            if missing:
                raise ValueError(f"message[{conversation_index}:{message_index}] 누락 필드: {sorted(missing)}")
            if message.get("memory_type") != "stm":
                raise ValueError(f"message[{conversation_index}:{message_index}] memory_type은 stm이어야 합니다.")
            if message.get("session_id") != conversation["session_id"]:
                raise ValueError(f"message[{conversation_index}:{message_index}] session_id가 대화와 다릅니다.")
            message_count += 1
    return {"session_count": len(conversations), "message_count": message_count}

LTM_MEMORY_SCHEMA = {
    "ltm_memory": [
        {
            "id": "string", # aaaaa_001_0
            "session_id": "string", # aaaaa_001
            "summary": "string",
            "topic_tags": ["string"],
            "source_message_ids": ["string"],
            "source_turn_indices": ["integer"],
        }
    ]
}

LTM_PROMOTION_INSTRUCTIONS = """
당신은 뉴스기사 STM을 장기 기억 JSON으로 승격하는 메모리 정리자입니다.
반드시 JSON만 반환하고, 각 LTM 항목은 원본 id, source_article_ids, source_turn_indices를 보존하세요.
summary는 장기적으로 재사용할 학습 맥락을 한국어 한두 문장으로 요약하세요.
topic_tags는 검색과 챗봇 응답에 바로 쓸 수 있는 짧은 한국어 배열로 작성하세요.
""".strip()

def build_ltm_promotion_prompt(stm_payload):
    return f"""
{LTM_PROMOTION_INSTRUCTIONS}

[출력 스키마]
{json.dumps(LTM_MEMORY_SCHEMA, ensure_ascii=False, indent=2)}

[입력 STM]
{json.dumps(stm_payload, ensure_ascii=False, indent=2)}
""".strip()

def parse_gemini_json(response):
    text = getattr(response, "text", None) or response.candidates[0].content.parts[0].text
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(text)

def validate_ltm_payload(payload):
    required_ltm_keys = {"id", "session_id", "summary", "topic_tags", "source_message_ids", "source_turn_indices"}
    memories = payload.get("ltm_memory")
    if not isinstance(memories, list) or not memories:
        raise ValueError("ltm_memory는 비어 있지 않은 리스트여야 합니다.")
    stm_session_ids = {conversation["session_id"] for conversation in stm_conversations}
    stm_message_ids = {message["id"] for conversation in stm_conversations for message in conversation["articles"]}
    for index, item in enumerate(memories):
        missing = required_ltm_keys - item.keys()
        if missing:
            raise ValueError(f"ltm_memory[{index}] 누락 필드: {sorted(missing)}")
        if item["session_id"] not in stm_session_ids:
            raise ValueError(f"ltm_memory[{index}] session_id가 STM 원본에 없습니다.")
        if not str(item["summary"]).strip():
            raise ValueError(f"ltm_memory[{index}] summary는 비어 있을 수 없습니다.")
        for field in [ "topic_tags", "source_message_ids"]:
            if not isinstance(item[field], list) or not all(isinstance(value, str) for value in item[field]):
                raise ValueError(f"ltm_memory[{index}].{field}는 문자열 리스트여야 합니다.")
        if not item["source_message_ids"] or not set(item["source_message_ids"]).issubset(stm_message_ids):
            print(set(item["source_message_ids"]).issubset(stm_message_ids))
            raise ValueError(f"ltm_memory[{index}] source_message_ids가 STM 원본과 맞지 않습니다.")
        if not isinstance(item["source_turn_indices"], list) or not all(isinstance(value, int) for value in item["source_turn_indices"]):
            raise ValueError(f"ltm_memory[{index}].source_turn_indices는 정수 리스트여야 합니다.")
    return payload

def validate_generated_ltm_json_structure(payload):
    required_ltm_keys = ["id", "session_id", "summary", "topic_tags", "source_message_ids", "source_turn_indices"]
    memories = payload["ltm_memory"]
    return {
        "root_key_present": "ltm_memory" in payload,
        "ltm_memory_type": type(memories).__name__,
        "ltm_count": len(memories),
        "required_fields": required_ltm_keys,
        "field_presence_by_item": [
            {field: field in item for field in required_ltm_keys}
            for item in memories
        ],
    }

# 최종적으로 모델한테 stm -> ltm으로 요약하게 하는 단계
def promote_stm_with_gemini(stm_payload):
    if stm_payload != stm_data:
        raise ValueError("이 핸즈온 셀은 위에서 검증한 STM 입력만 승격합니다.")
    response = client.models.generate_content(model=PROMOTION_MODEL, contents=ltm_promotion_prompt, config={"response_mime_type": "application/json"})
    return response
    # return parse_gemini_json(response)

def compact_embedding(text: str) -> list[float]:
    response = client.models.embed_content(model=EMBEDDING_MODEL, contents=text)
    embedding = response.embeddings[0].values
    return [float(value) for value in embedding]

input_file_name_list = [f for f in os.listdir("./output_news") if f.endswith(".json")]
retry_files_list = glob("./output_news/retry_folder/*.json")
done_files_list = glob("./output_news/done_folder/*.json")

retry_dir = './output_news/retry_folder/'
done_dir = './output_news/done_folder/'
input_dir = './output_news/'

ltm_collection = ensure_ltm_vector_collection(chroma_path=CHROMA_STORE_PATH)

for input_files in tqdm(input_file_name_list):
    # INPUT_FILE_NAME = "news" + input_files.split('news')[-1]
    INPUT_STM_PATH = PROJECT_ROOT / "output_news" / input_files

    stm_data = json.loads(INPUT_STM_PATH.read_text(encoding="utf-8"))
    validation_summary = validate_stm_payload(stm_data)
    stm_conversations = stm_data["stm_articles"]
    message_count = validation_summary["message_count"]

    ltm_promotion_prompt = build_ltm_promotion_prompt(stm_data)
    try:
        response = promote_stm_with_gemini(stm_data)
        payload = parse_gemini_json(response)
    except json.JSONDecodeError as je:
        print(je)
        source_destination = os.path.join(input_dir, input_files)
        retry_destination = os.path.join(retry_dir, input_files)
        shutil.move(source_destination, retry_destination)
        continue
    except Exception as e:
        print(e)
        continue

    generated_ltm_memory = validate_ltm_payload(payload)
    ltm_required_field_report = validate_generated_ltm_json_structure(generated_ltm_memory)
    ltm_validation_summary = {"ltm_count": len(generated_ltm_memory["ltm_memory"]), "source": INPUT_STM_PATH.relative_to(PROJECT_ROOT).as_posix(), "model": PROMOTION_MODEL}
    ltm_validation_summary["required_fields"] = ltm_required_field_report["required_fields"]


    # GENERATED_LTM_PATH = PROJECT_ROOT / "demo" / f"generated_memory_ltm_{input_files}.json"
    GENERATED_LTM_PATH = PROJECT_ROOT / "demo" / f"generated_memory_ltm_{input_files}"
    GENERATED_LTM_PATH.write_text(json.dumps(generated_ltm_memory, ensure_ascii=False, indent=2), encoding="utf-8")
    saved_ltm_memory = json.loads(GENERATED_LTM_PATH.read_text(encoding="utf-8"))
    validate_ltm_payload(saved_ltm_memory)

    source_destination = os.path.join(input_dir, input_files)
    done_destination = os.path.join(done_dir, input_files)
    shutil.move(source_destination, done_destination)

    ltm_items = saved_ltm_memory["ltm_memory"]

    ltm_chroma_ids = [item.get("id") or f"gdg-ltm-{index}" for index, item in enumerate(ltm_items)]
    expected_ltm_metadata_by_id = {
    chroma_id: {
        "session_id": item["session_id"],
        "summary": item["summary"],
        "topic_tags": json.dumps(item.get("topic_tags", []), ensure_ascii=False),
        }
        for chroma_id, item in zip(ltm_chroma_ids, ltm_items)
    }
    
    if ltm_items:
        ltm_collection.upsert(
            ids=ltm_chroma_ids,
            documents=[item["summary"] for item in ltm_items],
            embeddings=[compact_embedding(item["summary"]) for item in ltm_items],
            metadatas=[expected_ltm_metadata_by_id[chroma_id] for chroma_id in ltm_chroma_ids],
        )
    
    ltm_chroma_readback = ltm_collection.get(
    ids=ltm_chroma_ids,
    include=["documents", "embeddings", "metadatas"],
    ) if ltm_items else {"ids": [], "documents": [], "embeddings": [], "metadatas": []}
    assert len(ltm_chroma_readback["ids"]) == len(ltm_items)
    assert ltm_chroma_readback["documents"] == [item["summary"] for item in ltm_items]
    assert len(ltm_chroma_readback["embeddings"]) == len(ltm_items)
    ltm_embedding_dimensions = [len(embedding) for embedding in ltm_chroma_readback["embeddings"]]
    if ltm_embedding_dimensions:
        assert all(dimension == ltm_embedding_dimensions[0] for dimension in ltm_embedding_dimensions)
    ltm_chroma_metadata_by_id = dict(zip(ltm_chroma_readback["ids"], ltm_chroma_readback["metadatas"]))
    assert ltm_chroma_metadata_by_id == expected_ltm_metadata_by_id

    ltm_chroma_count = ltm_collection.count()
    chroma_validation_summary = {
    "ltm_expected_count": len(ltm_items),
    "ltm_actual_count": ltm_chroma_count,
    "ltm_metadata": ltm_chroma_metadata_by_id,
    }

    print(chroma_validation_summary)