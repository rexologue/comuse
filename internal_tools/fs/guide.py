from common.tools.guides import domain_guide


domain_guide(
    "fs",
    agent="Пути файлов бери из ответа fs.list, не придумывай.",
    compiler="Конкретные файлы из разговора обобщай шагом fs.list с pattern и датами (modified_from / modified_to).",
)
