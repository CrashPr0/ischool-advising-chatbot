# Safety responses for faculty review

**Status: pending approval from Dr. Karen Wang.** Do not merge this pull request until she approves the student disclaimer in `templates/index.html` and every template below. The bot is not a counselor. It does not diagnose, does not decide whether a student should stay in school, and does not store crisis messages.

## What the bot does

Every student message is classified before keyword shortcuts, retrieval, and the model.

- **Crisis.** Suicidal thoughts, self-harm, wanting to die, intent to harm someone else, immediate danger, or abuse. The reply is only the crisis template. Retrieval and the model are skipped. The turn is not appended to caller-supplied history. `app.py` still saves chat turns today; the follow-up wiring there should skip any result whose `safety_tier` is `crisis`, and it should pass `safety_tier` through the JSON response.
- **Distress.** Overwhelmed, cannot cope, anxious or depressed, burnout, failing everything, or thinking about dropping out or leaving school. The reply starts with the distress template. If the same message also asks a procedural advising question (for example, how to withdraw from a class), the grounded advising answer follows the template. The eval question `distress-overwhelmed-stay-or-leave` is distress only.
- **None.** Ordinary advising questions, including "drop a class" by itself.

The model never writes crisis resources or phone numbers. An optional model label (`SAFETY_MODEL_ASSIST`) is off by default, can only raise a tier, and a deterministic crisis result is never downgraded. Message text for crisis and distress is not logged.

## What triggers each tier

Crisis examples: "I want to die", "I don't want to live", "I might hurt myself", "he's going to kill me", "I'm being abused", "quiero morir", "no quiero vivir", "我想自杀", "我不想活了".

Negation cancels a direct phrase ("I do not want to die", "I'm not suicidal", "我不想死") except phrases that are themselves crisis ("I don't want to live", "no quiero vivir", "不想活"). Ambiguous self-harm ("I might hurt myself", "just kill me") is crisis.

Not crisis: "kill the process" (none), "this deadline is killing me" (distress), "dead week" (none), "shoot me an email" (none), "drop a class" by itself (none), "in danger of failing" (distress).

Distress examples: "I am overwhelmed", "I can't cope", "I'm thinking of dropping out", "I am falling behind and I do not think I can keep going".

## Verified resources

Fetched from the official pages on 2026-09-26. Numbers and names below are the only ones in the templates.

| Resource | What the page says | Source |
| --- | --- | --- |
| 988 Suicide & Crisis Lifeline | Call, text, or chat 988, 24/7 | https://988lifeline.org/ |
| CAPS, Student Wellness Center | Counseling & Psychological Services. Same-day support: drop in or call 408-924-5678 during business hours. Self-scheduled same-day telehealth each morning, Monday–Friday, in the SWC Patient Portal. After business hours, follow the emergency page. | https://www.sjsu.edu/wellness/access-services/counseling/index.php |
| Same-day appointments | Same-day appointments each day; new slots open every morning. Call (408) 924-5678. After-hours care is on the emergency page. | https://www.sjsu.edu/wellness/access-services/make-an-appointment.php |
| After-hours crisis | Life-threatening: call 911 or the nearest emergency room. Urgent mental health: call or text 988. After hours, urgent medical or mental health support: call 408-924-5678. | https://www.sjsu.edu/wellness/access-services/emergency-crisis.php |
| SJSU University Police | Emergency: call 911, or UPD at 408-924-2222 on any phone. | https://www.sjsu.edu/police/ |
| iSchool advising | Advisor appointments, including Spartan Connect. | https://ischool.sjsu.edu/bsisda-advising and https://one.sjsu.edu/task/all/spartan-connect |
| Drop a class | MySJSU drop steps. | https://www.sjsu.edu/registrar/registration/registration-basics/drop-a-class.php |
| Late drop and semester withdrawal | Undergraduate Education petitions for a late single-course drop and a semester withdrawal. | https://www.sjsu.edu/ue/student-petitions/drops/index.php |
| Leave of absence | One Semester Leave when eligible; a Leave of Absence Request for two or more consecutive semesters (maximum four). | https://www.sjsu.edu/registrar/registration/leave-of-absence.php |

Left out on purpose: Trans Lifeline 877-565-8860 is listed on the emergency page and is not in these templates. Unidentified sitewide numbers (including 408-924-1000 and 408-924-7786) are not used. Undergraduate Education's office line (408) 924-2447 and the registrar line 408-283-7500 option 3 are in the corpus pages, not in the student templates. Seasonal petition dates are not copied into the templates.

## Templates

These are the exact strings the bot sends. Links use `https`, `tel:`, and `sms:` so the chat page can make them clickable.

### Crisis, English

```
I'm glad you told me. You deserve support right now, and you do not have to handle this alone.

If you are in immediate danger, call 911.

The 988 Suicide & Crisis Lifeline is free, confidential, and available 24/7. Call [988](tel:988) or text [988](sms:988). You can also visit https://988lifeline.org/.

SJSU Counseling and Psychological Services (CAPS) is in the Student Wellness Center. Call [408-924-5678](tel:4089245678). During business hours you can drop in or call for same-day support, and same-day telehealth visits open each morning in the SWC Patient Portal: https://www.sjsu.edu/wellness/access-services/counseling/index.php. After hours, call [408-924-5678](tel:4089245678) for urgent mental health support: https://www.sjsu.edu/wellness/access-services/emergency-crisis.php.

SJSU University Police: call 911 or [408-924-2222](tel:4089242222). https://www.sjsu.edu/police/
```

### Crisis, Spanish

```
Me alegra que lo hayas dicho. Mereces apoyo ahora, y no tienes que enfrentarlo solo.

Si estás en peligro inmediato, llama al 911.

La línea 988 Suicide & Crisis Lifeline es gratuita, confidencial y está disponible las 24 horas. Llama al [988](tel:988) o envía un mensaje de texto al [988](sms:988). También puedes visitar https://988lifeline.org/.

SJSU Counseling and Psychological Services (CAPS), en el Student Wellness Center, atiende en el [408-924-5678](tel:4089245678). En horario de oficina puedes pasar sin cita o llamar para apoyo el mismo día, y cada mañana se abren citas de teleconsulta del mismo día en el SWC Patient Portal: https://www.sjsu.edu/wellness/access-services/counseling/index.php. Fuera de horario, llama al [408-924-5678](tel:4089245678) para apoyo urgente de salud mental: https://www.sjsu.edu/wellness/access-services/emergency-crisis.php.

Policía universitaria de SJSU: llama al 911 o al [408-924-2222](tel:4089242222). https://www.sjsu.edu/police/
```

### Crisis, Simplified Chinese

```
你愿意说出来，这很重要。你值得被支持，不必一个人扛着。

如果正处于即时危险中，请拨打 911。

988 自杀与心理危机生命线免费、保密，全天可用。请拨打 [988](tel:988) 或发送短信 [988](sms:988)，也可以访问 https://988lifeline.org/。

圣何塞州立大学 Counseling and Psychological Services（CAPS）位于 Student Wellness Center，电话 [408-924-5678](tel:4089245678)。办公时间内可以上门或致电获得当日支持，每天早上也可在 SWC Patient Portal 预约当日远程咨询：https://www.sjsu.edu/wellness/access-services/counseling/index.php。非办公时间的紧急心理健康支持请拨打 [408-924-5678](tel:4089245678)：https://www.sjsu.edu/wellness/access-services/emergency-crisis.php。

SJSU 校警紧急电话：拨打 911 或 [408-924-2222](tel:4089242222)。https://www.sjsu.edu/police/
```

### Distress, English

```
I'm sorry you're carrying this much. You do not have to decide today whether to stay or leave, and this chat cannot make that decision for you.

Counseling and Psychological Services (CAPS) at the Student Wellness Center can help. For same-day support, drop in or call [408-924-5678](tel:4089245678) during business hours. Same-day telehealth times open each morning, Monday through Friday, in the SWC Patient Portal.
https://www.sjsu.edu/wellness/access-services/counseling/index.php
Same-day appointments: https://www.sjsu.edu/wellness/access-services/make-an-appointment.php

You can also talk with an iSchool advisor. Current students schedule in Spartan Connect: https://one.sjsu.edu/task/all/spartan-connect
iSchool advising: https://ischool.sjsu.edu/bsisda-advising

These academic options exist if you want them:
- Drop a class in MySJSU: https://www.sjsu.edu/registrar/registration/registration-basics/drop-a-class.php
- Late drop or semester withdrawal: https://www.sjsu.edu/ue/student-petitions/drops/index.php
- Leave of absence: https://www.sjsu.edu/registrar/registration/leave-of-absence.php
```

### Distress, Spanish

```
Siento que estés cargando con tanto. No tienes que decidir hoy si quedarte o irte, y este chat no puede decidirlo por ti.

Counseling and Psychological Services (CAPS), en el Student Wellness Center, ofrece apoyo el mismo día. Pasa sin cita o llama al [408-924-5678](tel:4089245678) en horario de oficina. Las citas de teleconsulta del mismo día se abren cada mañana, de lunes a viernes, en el SWC Patient Portal.
https://www.sjsu.edu/wellness/access-services/counseling/index.php
Citas del mismo día: https://www.sjsu.edu/wellness/access-services/make-an-appointment.php

También puedes hablar con un asesor de iSchool. Estudiantes actuales reservan en Spartan Connect: https://one.sjsu.edu/task/all/spartan-connect
Asesoría de iSchool: https://ischool.sjsu.edu/bsisda-advising

Estas opciones académicas existen si las quieres:
- Dar de baja una clase en MySJSU: https://www.sjsu.edu/registrar/registration/registration-basics/drop-a-class.php
- Baja tardía o retiro del semestre: https://www.sjsu.edu/ue/student-petitions/drops/index.php
- Permiso de ausencia (leave of absence): https://www.sjsu.edu/registrar/registration/leave-of-absence.php
```

### Distress, Simplified Chinese

```
你现在承受的已经很多了。你不必今天就决定留下还是离开，这个对话也不能替你做这个决定。

Student Wellness Center 的 Counseling and Psychological Services（CAPS）可以提供帮助。办公时间内可以上门，或拨打 [408-924-5678](tel:4089245678) 获得当日支持。周一到周五每天早上，SWC Patient Portal 会开放当日远程咨询时段。
https://www.sjsu.edu/wellness/access-services/counseling/index.php
当日预约：https://www.sjsu.edu/wellness/access-services/make-an-appointment.php

你也可以和 iSchool 顾问谈谈。在读学生通过 Spartan Connect 预约：https://one.sjsu.edu/task/all/spartan-connect
iSchool 咨询：https://ischool.sjsu.edu/bsisda-advising

如果需要，学校有这些学业办法：
- 在 MySJSU 退选一门课：https://www.sjsu.edu/registrar/registration/registration-basics/drop-a-class.php
- 逾期退课或学期退学申请：https://www.sjsu.edu/ue/student-petitions/drops/index.php
- 休学（leave of absence）：https://www.sjsu.edu/registrar/registration/leave-of-absence.php
```

