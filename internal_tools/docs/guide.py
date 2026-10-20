from common.tools.guides import domain_guide


domain_guide(
    "docs",
    agent="""
Отчёт «такой же, как пример»:
1) найди файл-пример и файлы с данными за нужный период;
2) docs.make_template из файла-примера — получишь поля шаблона и example_context;
3) числа и текст для шаблона собери командами других групп (суммы — инструментом для таблиц, не в уме);
4) период отчёта бери из дат периода, а не из дат файлов;
5) docs.fill_template с этим шаблоном: ключи context — как в example_context;
6) сообщи пользователю итог и путь к готовому файлу.""",
    compiler="""
Если в разговоре шаблон уже создан (docs.make_template), не создавай его заново — используй готовый путь data:templates/….
Ключи context в docs.fill_template — ровно поля шаблона (fields и tables из docs.make_template).""",
)
