import os
from pathlib import Path
from glob import glob
from tqdm import tqdm
import json
from google import genai
from google.genai import errors as genai_errors
import chromadb
from chromadb.config import Settings
import shutil

from utils.db_utils import ensure_ltm_vector_collection

# ltm -> chromadb 적재하는 코드

try:
    PROJECT_ROOT = Path(__file__).resolve().parent
except NameError:
    PROJECT_ROOT = Path.cwd()

CHROMA_STORE_PATH = PROJECT_ROOT / "demo" / "chroma_gemini_handson_1"
PROMOTION_MODEL = "gemini-3.5-flash"
CHATBOT_MODEL = "gemini-3.5-flash"
EMBEDDING_MODEL = "gemini-embedding-001"

GEMINI_API_KEY = ""

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


def compact_embedding(text: str) -> list[float]:
    response = client.models.embed_content(model=EMBEDDING_MODEL, contents=text)
    embedding = response.embeddings[0].values
    return [float(value) for value in embedding]

LTM_SOURCE_DIR = PROJECT_ROOT / "new_demo"
LTM_DONE_DIR = PROJECT_ROOT / "new_demo" / "done_folder"

RETRYABLE_STATUS_CODES = {429, 503}

ltm_files = sorted(
        f for f in os.listdir(LTM_SOURCE_DIR) if f.startswith("generated_memory_ltm_news_")
    )

ltm_collection = ensure_ltm_vector_collection(chroma_path=CHROMA_STORE_PATH)

for input_files in tqdm(ltm_files):
    INPUT_LTM_PATH = LTM_SOURCE_DIR / input_files
    ltm_data = json.loads(INPUT_LTM_PATH.read_text(encoding="utf-8"))

    ltm_items = ltm_data["ltm_memory"]

    ltm_chroma_ids = [item.get("id") or f"gdg-ltm-{index}" for index, item in enumerate(ltm_items)]
    expected_ltm_metadata_by_id = {
    chroma_id: {
        "pubDate": item["pubDate"],
        }
        for chroma_id, item in zip(ltm_chroma_ids, ltm_items)
    }
    
    try:
        if ltm_items:
            ltm_collection.upsert(
                ids=ltm_chroma_ids,
                documents=[item["summary"] for item in ltm_items],
                embeddings=[compact_embedding(item["summary"]) for item in ltm_items],
                metadatas=[expected_ltm_metadata_by_id[chroma_id] for chroma_id in ltm_chroma_ids],
            )
    # except Exception as e:
    #     # gemini api 503, 429 에러 시 예외 처리
    #     print(e)
    #     continue
    except genai_errors.APIError as e:
        print(e)
        status_code = getattr(e, "code", None)
        if status_code not in RETRYABLE_STATUS_CODES:  # {429, 503}
            raise
    # 지수 백오프(1.5s, 3s, 6s, 12s, 24s...) + 지터로 재시도
    
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

    src = LTM_SOURCE_DIR / input_files
    dst = LTM_DONE_DIR / input_files
    shutil.move(str(src), str(dst))