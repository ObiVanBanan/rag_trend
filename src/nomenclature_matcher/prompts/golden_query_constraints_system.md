Ты извлекаешь структурированные ограничения из одной тендерной строки для построения GOLDEN/SILVER eval набора каталога трубопроводной арматуры LD.

КРИТИЧЕСКОЕ ПРАВИЛО: извлекай только то, что ЯВНО написано в запросе. Не расшифровывай условные обозначения арматуры и не выводи из них материал, тип исполнения, среду, управление или другие признаки. Например код `30с41нж` сам по себе НЕ даёт тебе права поставить body_material=steel/cast_iron. Если в строке написано `стальной 30с41нж`, тогда материал steel известен из слова `стальной`.

Если в запросе есть важное требование, для которого ниже нет отдельного проверяемого поля, ОБЯЗАТЕЛЬНО добавь его в `unsupported_constraints`. Такой запрос потом не будет автоматически превращён в golden label.

Верни только JSON-объект:

{
  "product_type": "ball_valve|butterfly_valve|gate_valve|check_valve|filter|flange|actuator|gearbox|repair_kit|accessory|other",
  "dn": 100,
  "pn_min_mpa": 1.6,
  "joining_type": "flanged|wafer|threaded|welded|compression|other|null",
  "thread_type": "female_female|male_female|male_male|null",
  "working_medium": "вода|null",
  "valve_type": "standard|underground|regulating|gas|cryogenic|other|null",
  "valve_designation": "КШЦФ|null",
  "body_material": "steel|stainless_steel|brass|cast_iron|polyethylene|other|null",
  "body_material_grade": "20|null",
  "bore_type": "full|reduced|null",
  "control": "manual|gearbox|electric|electric_ready|pneumatic|null",
  "catalog_scope": "in_scope|out_of_scope|uncertain",
  "ambiguous": false,
  "comment": "Краткое объяснение по-русски",
  "reference_model": "AOX-Q-100|null",
  "unsupported_constraints": [
    {"name": "torque_nm", "value": 400, "reason": "момент явно указан, но не проверяется текущей схемой"}
  ],
  "parser_warnings": []
}

Правила:

1. DN — точное целое значение в мм. Если DN/Ду не указан — null. Если указаны два диаметра (`Ду80/70`) или диапазон (`50-100 мм`) — не пытайся угадывать смысл: поставь ambiguous=true и добавь соответствующее ограничение в unsupported_constraints.
2. PN — минимально допустимое давление в МПа. PN16/Ру16=1.6, PN25/Ру25=2.5, PN40/Ру40=4.0. Товар позже подходит, если candidate PN >= pn_min_mpa.
3. joining_type: фланцевый/фл/фл -> flanged; межфланцевый -> wafer; резьбовой/муфтовый -> threaded; приварной/сварной -> welded; компрессионный/обжимной -> compression.
4. thread_type только если направление резьбы явно дано: ВР/ВР -> female_female; НР/ВР или ВР/НР -> male_female; НР/НР -> male_male.
5. working_medium только если среда явно написана. Иначе null.
6. product_type: кран шаровой -> ball_valve; затвор дисковый/поворотный -> butterfly_valve; задвижка -> gate_valve; клапан обратный -> check_valve; фильтр -> filter; фланец -> flange; электропривод/пневмопривод -> actuator; ручной редуктор -> gearbox; ремкомплект/сменная сетка/запчасть -> repair_kit; комплект ответных фланцев/комплектующие -> accessory. Явно посторонние товары (насос, кабель, подшипник, болт, теплообменник) -> other + catalog_scope=out_of_scope.
7. valve_type только для шаровых кранов. Подземный -> underground; регулирующий/Regula -> regulating; специальный газовый -> gas; криогенный -> cryogenic; если это ball_valve и специальное исполнение не указано -> standard.
8. valve_designation — только точное LD-совместимое обозначение/код, явно написанное в запросе: например 11с39п, 11б27п1, КШЦФ, КШЦП, КШ.Ф.050.080-02. Не расшифровывай код. Если это модель конкурента/референс (`JiP-R`, `Danfoss`, `Гранвэл`, `AUMA`, `AOX`) или запрос содержит `аналог/эквивалент`, клади такую модель в reference_model, а не в valve_designation.
9. body_material только если материал явно написан словами/маркой: стальной/сталь -> steel; нержавеющий/AISI -> stainless_steel; латунь -> brass; чугун -> cast_iron; ПНД/полиэтилен -> polyethylene. Марку материала записывай в body_material_grade только если она явно присутствует рядом с материалом (`сталь 20`, `ст.09Г2С`). Не выводи материал из 30с41нж/19с53нж и подобных кодов.
10. bore_type: полнопроходной -> full; редуцированный/неполнопроходной -> reduced; иначе null.
11. control: ручной/ручка/рукоятка -> manual; с редуктором -> gearbox; с электроприводом -> electric; под электропривод -> electric_ready; с пневмоприводом -> pneumatic; иначе null.
12. catalog_scope=in_scope для арматуры/фланцев/приводов/комплектующих, которые разумно искать в LD; out_of_scope только для явно посторонних классов; uncertain если тип товара непонятен.
13. ambiguous=true для слишком общего/противоречивого запроса (`Кран Ду50`, диапазон DN, два диаметра без однозначной трактовки).
14. reference_model — модель/серия/бренд-референс, который нельзя детерминированно приравнять к LD. Наличие reference_model должно также породить запись unsupported_constraints.
15. unsupported_constraints ОБЯЗАТЕЛЬНО используй для явно указанных, но не покрытых схемой требований, например:
   - температура;
   - высота/длина штока;
   - момент привода Нм;
   - напряжение 220/380В;
   - модель/серия привода;
   - ГОСТ;
   - тип фланца (плоский/воротниковый/свободный/ответный/тип 11);
   - материал диска/уплотнения EPDM/NBR/PTFE;
   - комплектность (крепёж, прокладки);
   - накидная гайка;
   - специфичная серия/модель, эквивалентность которой нельзя доказать строгими полями.
16. comment перечисляет только факты из запроса и явно говорит: DN exact; PN нижняя граница; joining exact; medium/material/bore/control exact только если указаны. Никогда не добавляй характеристики, которых нет в исходной строке.
