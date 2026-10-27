"""Language codes and deterministic document chrome; never default to English silently."""
import re
from datetime import datetime
from opencc import OpenCC

ALIASES = {'zh': 'zh-CN', 'zh-cn':'zh-CN', 'zh-hans':'zh-CN', 'zh-sg':'zh-CN',
           'zh-tw':'zh-TW', 'zh-hant':'zh-TW', 'zh-hk':'zh-TW', 'zh-mo':'zh-TW'}
LANGUAGE_NAMES = {'en':'English', 'zh-CN':'简体中文', 'zh-TW':'繁體中文', 'ja':'日本語',
                  'ko':'한국어', 'fr':'Français', 'de':'Deutsch', 'es':'Español',
                  'it':'Italiano', 'pt':'Português', 'ru':'Русский', 'ar':'العربية',
                  'th':'ภาษาไทย', 'vi':'Tiếng Việt', 'id':'Bahasa Indonesia', 'hi':'हिन्दी'}
_CONVERTERS = {'zh-CN': OpenCC('t2s'), 'zh-TW': OpenCC('s2t')}


def normalize_language(code):
    value = str(code or '').strip().replace('_', '-')
    canonical = ALIASES.get(value.lower(), value.lower())
    if canonical not in LANGUAGE_NAMES:
        raise ValueError(f'不支持的目标语言：{code}')
    return canonical


def chinese_text(text, language):
    text = str(text)
    converter = _CONVERTERS.get(normalize_language(language))
    return converter.convert(text) if converter else text


def chinese_field_label(name, data, translated_label, language):
    # Legacy results were forced to use English labels. Recover the semantic
    # Chinese source label, not type annotations such as string/date/number.
    candidates = (data.get('field_type', ''), name, translated_label)
    for candidate in candidates:
        if re.search(r'[\u3400-\u9fff]', str(candidate)):
            return chinese_text(candidate, language)
    return str(translated_label or data.get('field_type') or name)


DEFAULT_TITLES = {'en':'Document', 'zh-CN':'文档', 'zh-TW':'文件', 'ja':'書類', 'ko':'문서',
 'fr':'Document', 'de':'Dokument', 'es':'Documento', 'it':'Documento', 'pt':'Documento',
 'ru':'Документ', 'ar':'مستند', 'th':'เอกสาร', 'vi':'Tài liệu', 'id':'Dokumen', 'hi':'दस्तावेज़'}
TITLE_TRANSLATIONS = {
 '驾驶证': {'en':"Driver's License",'ja':'運転免許証','ko':'운전면허증','fr':'Permis de conduire','de':'Führerschein','es':'Permiso de conducir','ru':'Водительское удостоверение'},
 '出生医学证明': {'en':'Birth Certificate','ja':'出生証明書','ko':'출생증명서','fr':'Acte de naissance','de':'Geburtsurkunde','es':'Certificado de nacimiento','ru':'Свидетельство о рождении'},
 '户口簿': {'en':'Household Register','ja':'戸籍謄本','ko':'호적등본','fr':'Registre de ménage','de':'Haushaltsregister','es':'Registro del hogar','ru':'Домовая книга'},
 '结婚证': {'en':'Marriage Certificate','ja':'結婚証明書','ko':'혼인증명서','fr':'Certificat de mariage','de':'Heiratsurkunde','es':'Certificado de matrimonio','ru':'Свидетельство о браке'},
 '亲属关系证明': {'en':'Kinship Certificate','ja':'親族関係証明書','ko':'친족관계증명서','fr':'Certificat de parenté','de':'Verwandtschaftsbescheinigung','es':'Certificado de parentesco','ru':'Справка о родстве'},
 '无犯罪记录证明': {'en':'Certificate of No Criminal Record','ja':'犯罪経歴証明書','ko':'범죄경력증명서','fr':'Certificat de casier judiciaire','de':'Führungszeugnis','es':'Certificado de antecedentes penales','ru':'Справка об отсутствии судимости'},
 '毕业证书': {'en':'Diploma','ja':'卒業証明書','ko':'졸업증명서','fr':'Diplôme','de':'Diplom','es':'Diploma','ru':'Диплом'},
 '声明书': {'en':'Declaration','ja':'宣言書','ko':'선언서','fr':'Déclaration','de':'Erklärung','es':'Declaración','ru':'Заявление'},
 '公证书': {'en':'Notarial Certificate','ja':'公正証書','ko':'공증서','fr':'Acte notarié','de':'Notarielle Urkunde','es':'Acta notarial','ru':'Нотариальное свидетельство'}
}


def document_title(source_title, language):
    language = normalize_language(language)
    source_title = str(source_title or '').strip()
    if language.startswith('zh-'):
        return chinese_text(source_title, language) if source_title else DEFAULT_TITLES[language]
    return TITLE_TRANSLATIONS.get(source_title, {}).get(language, DEFAULT_TITLES[language])


_MONTHS = {
 'en':'January February March April May June July August September October November December'.split(),
 'fr':'janvier février mars avril mai juin juillet août septembre octobre novembre décembre'.split(),
 'es':'enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre'.split(),
 'de':'Januar Februar März April Mai Juni Juli August September Oktober November Dezember'.split(),
 'it':'gennaio febbraio marzo aprile maggio giugno luglio agosto settembre ottobre novembre dicembre'.split(),
 'pt':'janeiro fevereiro março abril maio junho julho agosto setembro outubro novembro dezembro'.split(),
 'ru':'января февраля марта апреля мая июня июля августа сентября октября ноября декабря'.split()}


def document_date(language, value=None):
    language = normalize_language(language)
    value = value or datetime.now()
    year, month, day = value.year, value.month, value.day
    if language in ('zh-CN','zh-TW','ja'): return f'{year}年{month:02d}月{day:02d}日'
    if language == 'ko': return f'{year}년 {month:02d}월 {day:02d}일'
    if language in _MONTHS:
        name = _MONTHS[language][month-1]
        if language == 'en': return f'{name} {day}, {year}'
        if language in ('es','pt'): return f'{day} de {name} de {year}'
        if language == 'de': return f'{day}. {name} {year}'
        if language == 'ru': return f'{day} {name} {year} г.'
        return f'{day} {name} {year}'
    return f'{year:04d}-{month:02d}-{day:02d}'
