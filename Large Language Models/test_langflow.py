from langflow import load_flow_from_json
TWEAKS = [
  {
    "VectorStoreAgent-FFL2g": {},
    "VectorStoreInfo-gVNWh": {},
    "OpenAIEmbeddings-BdzJl": {},
    "Chroma-alkQH": {},
    "RecursiveCharacterTextSplitter-tiUiA": {},
    "ChatOpenAI-XNwpk": {},
    "PyPDFLoader-2KrOM": {}
  }
]
flow = load_flow_from_json("PDF Loader.json", tweaks=TWEAKS)
# Now you can use it like any chain
flow("Hey, have you heard of LangFlow?")
