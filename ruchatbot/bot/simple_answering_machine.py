# -*- coding: utf-8 -*-

import json
import os
import math
import logging
import numpy as np
import operator

from ruchatbot.bot.base_answering_machine import BaseAnsweringMachine
from ruchatbot.bot.simple_dialog_session_factory import SimpleDialogSessionFactory
from ruchatbot.bot.word_embeddings import WordEmbeddings
# from xgb_relevancy_detector import XGB_RelevancyDetector
from ruchatbot.bot.lgb_relevancy_detector import LGB_RelevancyDetector
#from nn_relevancy_tripleloss import NN_RelevancyTripleLoss
#from xgb_person_classifier_model import XGB_PersonClassifierModel
#from nn_person_change import NN_PersonChange
from ruchatbot.bot.answer_builder import AnswerBuilder
from ruchatbot.bot.interpreted_phrase import InterpretedPhrase
from ruchatbot.bot.nn_enough_premises_model import NN_EnoughPremisesModel
# from nn_synonymy_detector import NN_SynonymyDetector
from ruchatbot.bot.lgb_synonymy_detector import LGB_SynonymyDetector
# from nn_synonymy_tripleloss import NN_SynonymyTripleLoss
from ruchatbot.bot.jaccard_synonymy_detector import Jaccard_SynonymyDetector
from ruchatbot.bot.nn_interpreter import NN_Interpreter
from ruchatbot.bot.nn_req_interpretation import NN_ReqInterpretation
from ruchatbot.bot.modality_detector import ModalityDetector
from ruchatbot.bot.simple_modality_detector import SimpleModalityDetectorRU
from ruchatbot.bot.no_information_model import NoInformationModel
from ruchatbot.bot.intent_detector import IntentDetector
from ruchatbot.generative_grammar.generative_grammar_engine import GenerativeGrammarEngine
from ruchatbot.bot.entity_extractor import EntityExtractor


class InsteadofRuleResult(object):
    def __init__(self):
        self.applied = None
        self.replica_is_generated = None

    @staticmethod
    def GetTrue(replica_is_generated):
        res = InsteadofRuleResult()
        res.applied = True
        res.replica_is_generated = replica_is_generated
        return res

    @staticmethod
    def GetFalse():
        res = InsteadofRuleResult()
        res.applied = False
        return res


def same_stem2(word, key_stems):
    for stem in key_stems:
        if stem in word:
            return True
    return False


class SimpleAnsweringMachine(BaseAnsweringMachine):
    """
    Движок чатбота на основе набора нейросетевых и прочих моделей (https://github.com/Koziev/chatbot).
    Методы класса реализуют workflow обработки реплик пользователя - формирование ответов, управление
    базой знаний.
    """

    def __init__(self, text_utils):
        super(SimpleAnsweringMachine, self).__init__()
        self.trace_enabled = False
        self.session_factory = SimpleDialogSessionFactory()
        self.text_utils = text_utils
        self.logger = logging.getLogger('SimpleAnsweringMachine')

        # Если релевантность факта к вопросу в БФ ниже этого порога, то факт не подойдет
        # для генерации ответа на основе факта.
        self.min_premise_relevancy = 0.6
        self.min_faq_relevancy = 0.7

    def get_model_filepath(self, models_folder, old_filepath):
        """
        Для внутреннего использования - корректирует абсолютный путь
        к файлам данных модели так, чтобы был указанный каталог.
        """
        _, tail = os.path.split(old_filepath)
        return os.path.join(models_folder, tail)

    def load_models(self, data_folder, models_folder, w2v_folder):
        self.logger.info(u'Loading models from {}'.format(models_folder))
        self.models_folder = models_folder

        self.premise_not_found = NoInformationModel()
        self.premise_not_found.load(models_folder, data_folder)

        # Загружаем общие параметры для сеточных моделей
        with open(os.path.join(models_folder, 'qa_model_selector.config'), 'r') as f:
            model_config = json.load(f)
            self.max_inputseq_len = model_config['max_inputseq_len']
            self.wordchar2vector_path = self.get_model_filepath(models_folder, model_config['wordchar2vector_path'])
            self.PAD_WORD = model_config['PAD_WORD']
            self.word_dims = model_config['word_dims']

        self.qa_model_config = model_config

        # TODO: выбор конкретной реализации для каждого типа моделей сделать внутри базового класса
        # через анализ поля 'engine' в конфигурации модели. Для нейросетевых моделей там будет
        # значение 'nn', для градиентного бустинга - 'xgb'. Таким образом, уберем ненужную связность
        # данного класса и конкретных реализации моделей.

        # Определение релевантности предпосылки и вопроса на основе XGB модели
        # self.relevancy_detector = XGB_RelevancyDetector()
        self.relevancy_detector = LGB_RelevancyDetector()
        # self.relevancy_detector = NN_RelevancyTripleLoss()
        self.relevancy_detector.load(models_folder)

        # Модель определения синонимичности двух фраз
        # self.synonymy_detector = NN_SynonymyDetector()
        # self.synonymy_detector = NN_SynonymyTripleLoss()
        self.synonymy_detector = LGB_SynonymyDetector()
        self.synonymy_detector.load(models_folder)
        # self.synonymy_detector = Jaccard_SynonymyDetector()

        self.interpreter = NN_Interpreter()
        self.interpreter.load(models_folder)

        self.req_interpretation = NN_ReqInterpretation()
        self.req_interpretation.load(models_folder)

        # Определение достаточности набора предпосылок для ответа на вопрос
        self.enough_premises = NN_EnoughPremisesModel()
        self.enough_premises.load(models_folder)

        # Комплексная модель (группа моделей) для генерации текста ответа
        self.answer_builder = AnswerBuilder()
        self.answer_builder.load_models(models_folder, self.text_utils)

        # Генеративная грамматика для формирования реплик
        self.replica_grammar = GenerativeGrammarEngine()
        with open(os.path.join(models_folder, 'replica_generator_grammar.bin'), 'rb') as f:
            self.replica_grammar = GenerativeGrammarEngine.unpickle_from(f)
        self.replica_grammar.set_dictionaries(self.text_utils.gg_dictionaries)

        # Классификатор грамматического лица на базе XGB
        #self.person_classifier = XGB_PersonClassifierModel()
        #self.person_classifier.load(models_folder)

        # Нейросетевая модель для манипуляции с грамматическим лицом
        #self.person_changer = NN_PersonChange()
        #self.person_changer.load(models_folder)

        # Модель определения модальности фраз собеседника
        self.modality_model = SimpleModalityDetectorRU()
        self.modality_model.load(models_folder)

        self.intent_detector = IntentDetector()
        self.intent_detector.load(models_folder)

        self.entity_extractor = EntityExtractor()
        self.entity_extractor.load(models_folder)

        # Загрузка векторных словарей
        self.word_embeddings = WordEmbeddings()
        self.word_embeddings.load_models(models_folder)
        self.word_embeddings.load_wc2v_model(self.wordchar2vector_path)
        for p in self.answer_builder.get_w2v_paths():
            p = os.path.join(w2v_folder, os.path.basename(p))
            self.word_embeddings.load_w2v_model(p)

        w2v_path = self.relevancy_detector.get_w2v_path()
        if w2v_path is not None:
            self.word_embeddings.load_w2v_model(w2v_path)

        if self.premise_not_found.get_w2v_path():
            self.word_embeddings.load_w2v_model(self.premise_not_found.get_w2v_path())

        self.word_embeddings.load_w2v_model(os.path.join(w2v_folder, os.path.basename(self.enough_premises.get_w2v_path())))

        self.jsyndet = Jaccard_SynonymyDetector()

        self.logger.debug('All models loaded')

    def extract_entity(self, entity_name, phrase_str):
        return self.entity_extractor.extract_entity(entity_name, phrase_str, self.text_utils, self.word_embeddings)

    def start_conversation(self, bot, interlocutor):
        """
        Начало общения бота с interlocutor. Ни одной реплики еще не было.
        Бот может поприветствовать собеседника или напомнить ему что-то, если
        в сессии с ним была какая-то напоминалка, т.д. Фразу, которую надо показать собеседнику,
        поместим в буфер выходных фраз с помощью метода say, а внешний цикл обработки уже извлечет ее оттуда
        и напечатает в консоли и т.д.

        :param bot: экземпляр класса BotPersonality
        :param interlocutor: строковый идентификатор собеседника.
        :return: строка реплики, которую скажет бот.
        """
        session = self.get_session(bot, interlocutor)
        if bot.has_scripting():
            phrase = bot.scripting.start_conversation(self, session)
            if phrase is not None:
                self.say(session, phrase)

    def get_session_factory(self):
        return self.session_factory

    def is_question(self, phrase):
        modality, person = self.modality_model.get_modality(phrase, self.text_utils, self.word_embeddings)
        return modality == ModalityDetector.question

    def translate_interlocutor_replica(self, bot, session, raw_phrase):
        rules = bot.get_comprehension_templates().get_templates()
        order2anchor = dict((order, anchor) for (anchor, order) in rules)
        phrases = list(order for (anchor, order) in rules)
        phrases2 = list((self.text_utils.wordize_text(order), None, None) for (anchor, order) in rules)
        #canonized2raw = dict((f2[0], f1) for (f1, f2) in itertools.izip(phrases, phrases2))
        canonized2raw = dict((f2[0], f1) for (f1, f2) in zip(phrases, phrases2))

        raw_phrase2 = self.text_utils.wordize_text(raw_phrase)
        best_order, best_sim = self.synonymy_detector.get_most_similar(raw_phrase2,
                                                                       phrases2,
                                                                       self.text_utils,
                                                                       self.word_embeddings,
                                                                       nb_results=1)

        # Если похожесть проверяемой реплики на любой вариант в таблице приказов выше порога,
        # то дальше будем обрабатывать нормализованную фразу вместо исходной введенной.
        comprehension_threshold = 0.70
        if best_sim > comprehension_threshold:
            if self.trace_enabled:
                self.logger.info(
                    u'Closest comprehension phrase is "{}" with similarity={} above threshold={}'.format(best_order, best_sim, comprehension_threshold))

            interpreted_order = order2anchor[canonized2raw[best_order]]
            if raw_phrase2 != interpreted_order:
                if self.trace_enabled:
                    self.logger.info(u'Phrase "{}" is interpreted as "{}"'.format(raw_phrase, interpreted_order))
                return interpreted_order
            else:
                return None
        else:
            return None

    def interpret_phrase(self, bot, session, raw_phrase, internal_issuer):
        interpreted = InterpretedPhrase(raw_phrase)
        phrase = raw_phrase
        phrase_modality, phrase_person = self.modality_model.get_modality(phrase, self.text_utils, self.word_embeddings)
        phrase_is_question = phrase_modality == ModalityDetector.question

        # история фраз доступна в session как conversation_history
        was_interpreted = False

        last_phrase = session.conversation_history[-1] if len(session.conversation_history) > 0 else None

        if not internal_issuer:
            # Интерпретация вопроса собеседника (человека):
            # (H) Ты яблоки любишь?
            # (B) Да
            # (H) А виноград? <<----- == Ты виноград любишь?
            if len(session.conversation_history) > 1 and phrase_is_question:
                last2_phrase = session.conversation_history[-2]  # это вопрос человека "Ты яблоки любишь?"

                if not last2_phrase.is_bot_phrase\
                    and last2_phrase.is_question\
                    and self.interpreter is not None:

                    if self.req_interpretation.require_interpretation(raw_phrase,
                                                                      self.text_utils,
                                                                      self.word_embeddings):
                        context_phrases = list()
                        # Контекст состоит из двух предыдущих фраз
                        context_phrases.append(last2_phrase.raw_phrase)
                        context_phrases.append(last_phrase.raw_phrase)
                        context_phrases.append(raw_phrase)
                        phrase = self.interpreter.interpret(context_phrases, self.text_utils, self.word_embeddings)

                        if self.intent_detector is not None:
                            interpreted.intent = self.intent_detector.detect_intent(raw_phrase, self.text_utils,
                                                                                    self.word_embeddings)
                            self.logger.debug(u'intent="%s"', interpreted.intent)

                        was_interpreted = True

            # and last_phrase.is_question\
            if not was_interpreted\
                    and len(session.conversation_history) > 0\
                    and last_phrase.is_bot_phrase\
                    and not phrase_is_question\
                    and self.interpreter is not None:

                if self.req_interpretation.require_interpretation(raw_phrase,
                                                                  self.text_utils,
                                                                  self.word_embeddings):
                    # В отдельной ветке обрабатываем ситуацию, когда бот
                    # задал вопрос или квази-вопрос типа "А давай xxx", на который собеседник дал краткий ответ.
                    # с помощью специальной модели мы попробуем восстановить полный
                    # текст ответа собеседника.
                    context_phrases = list()
                    context_phrases.append(last_phrase.interpretation)
                    context_phrases.append(raw_phrase)
                    phrase = self.interpreter.interpret(context_phrases, self.text_utils, self.word_embeddings)
                    if self.intent_detector is not None:
                        interpreted.intent = self.intent_detector.detect_intent(raw_phrase, self.text_utils,
                                                                                self.word_embeddings)
                        self.logger.debug(u'intent="%s"', interpreted.intent)

                    was_interpreted = True

        if not interpreted.intent:
            if self.intent_detector is not None:
                interpreted.intent = self.intent_detector.detect_intent(raw_phrase, self.text_utils,
                                                                        self.word_embeddings)
                self.logger.debug(u'intent="%s"', interpreted.intent)

        if was_interpreted:
            phrase = self.interpreter.normalize_person(phrase, self.text_utils, self.word_embeddings)

        if not internal_issuer:
            # Попробуем найти шаблон трансляции, достаточно похожий на эту фразу.
            # Может получиться так, что введенная императивная фраза станет обычным вопросом:
            # "назови свое имя!" ==> "Как тебя зовут?"
            translated_str = self.translate_interlocutor_replica(bot, session, phrase)
            if translated_str is not None:
                phrase = translated_str
                raw_phrase = translated_str
                phrase_modality, phrase_person = self.modality_model.get_modality(phrase, self.text_utils, self.word_embeddings)
                was_interpreted = True

        if not was_interpreted:
            phrase = self.interpreter.normalize_person(raw_phrase, self.text_utils, self.word_embeddings)

        # TODO: Если результат интерпретации содержит мусор, то не нужно его обрабатывать.
        # Поэтому тут надо проверить phrase  с помощью верификатора синтаксиса.
        # ...

        interpreted.interpretation = phrase
        interpreted.set_modality(phrase_modality, phrase_person)

        return interpreted

    def say(self, session, answer):
        self.logger.info(u'say "%s"', answer)
        answer_interpretation = InterpretedPhrase(answer)
        answer_interpretation.is_bot_phrase = True
        answer_interpretation.set_modality(*self.modality_model.get_modality(answer, self.text_utils, self.word_embeddings))
        session.add_to_buffer(answer)
        session.add_phrase_to_history(answer_interpretation)

    def does_bot_know_answer(self, question, bot, session, interlocutor):
        """Вернет true, если бот знает ответ на вопрос question"""
        memory_phrases = list(bot.facts.enumerate_facts(interlocutor))
        best_premise, best_rel = self.relevancy_detector.get_most_relevant(question,
                                                                             memory_phrases,
                                                                             self.text_utils,
                                                                             self.word_embeddings,
                                                                             nb_results=1)
        return best_rel >= self.min_premise_relevancy

    def calc_discourse_relevance(self, replica, session):
        """Возвращает оценку соответствия реплики replica текущему дискурсу беседы session"""
        # TODO
        return 1.0

    def bot_replica_already_uttered(self, bot, session, phrase):
        """Проверяем, была ли такая же или синонимичная реплика уже сказана ботом ранее"""
        found_same_replica = session.count_bot_phrase(phrase) > 0
        if not found_same_replica:
            # В точности такой же реплики не было, но надо проверить на перефразировки.
            bot_phrases = [(f, None, None) for f in session.get_bot_phrases()]
            if len(bot_phrases) > 0:
                best_phrase, best_rel = self.synonymy_detector.get_most_similar(phrase, bot_phrases,
                                                                                self.text_utils,
                                                                                self.word_embeddings,
                                                                                nb_results=1)
                if best_rel >= self.synonymy_detector.get_threshold():
                    found_same_replica = True
        return found_same_replica

    def generate_with_generative_grammar(self, bot, session, interlocutor, phrase, base_weight):
        # Используем генеративную грамматику для получения возможных реплик
        self.logger.debug('Using replica_grammar to generate replicas...')
        generated_replicas = []
        words = self.text_utils.tokenize(phrase)
        all_generated_phrases = self.replica_grammar.generate(words, self.text_utils.known_words, use_assocs=False)

        for replica in sorted(all_generated_phrases, key=lambda z: -z.get_rank())[:5]:
            replica_str = replica.get_str()
            if not self.bot_replica_already_uttered(bot, session, replica_str):
                # проверить, если replica_str является репликой-ответом: знает
                # ли бот ответ на этот вопрос.
                good_replica = True
                if replica_str[-1] == u'?':
                    if self.does_bot_know_answer(replica_str, bot, session, interlocutor):
                        good_replica = False

                if good_replica:
                    discourse_rel = self.calc_discourse_relevance(replica_str, session)
                    # TODO - добавить сюда еще взвешивание по модели синтаксической валидации
                    replica_w = discourse_rel * replica.get_rank()
                    generated_replicas.append((replica.get_str(), replica_w, 'generate_with_generative_grammar'))
                    break

        return generated_replicas


    def generate_with_common_phrases(self, bot, session, interlocutor, phrase, base_weight):
        generated_replicas = []

        # Более тяжелый алгоритм поиск подходящей реплики: ищем такую фразу, в которой
        # есть не менее одного существительного или глагола, общего с входной репликой.
        # Для этого нам надо выполнить частеречную разметку.
        phrase_words = self.text_utils.tokenize(phrase)
        phrase_tags = self.text_utils.tag(phrase_words)
        key_stems = set()
        for token in phrase_tags:
            if u'NOUN' in token[1] or u'VERB' in token[1]:
                if len(token[0]) >= 4:
                    stem = token[0][:4]
                    key_stems.add(stem)
        common_phrase_weights = []
        max_weight = 0
        for phrase2 in bot.get_common_phrases():
            words2 = self.text_utils.tokenize(phrase2)
            stem_hits = sum(same_stem2(word, key_stems) for word in words2)
            if stem_hits >= 1:
                common_phrase_weights.append((phrase2, stem_hits))
                if stem_hits > max_weight:
                    max_weight = stem_hits

        # Берем все фразы, у которых max_weight вхождений стемов.
        best_phrases = [f for (f, w) in common_phrase_weights if w == max_weight]

        # Теперь среди них найдем фразу, максимально похожую на исходную фразу
        best_phrases = [(f,) for f in best_phrases]

        if len(best_phrases) > 0:
            # TODO: возможно, вместо коэффициента Жаккара лучше использовать word mover's distance
            sim_phrases = self.jsyndet.get_most_similar(phrase, best_phrases, self.text_utils,
                                                        self.word_embeddings, nb_results=1)

            for f, phrase_sim in [sim_phrases]:
                if not self.bot_replica_already_uttered(bot, session, f):
                    # проверить, если f является репликой-ответом: знает
                    # ли бот ответ на этот вопрос.
                    good_replica = True
                    if f[-1] == u'?':
                        if self.does_bot_know_answer(f, bot, session, interlocutor):
                            good_replica = False

                    if good_replica:
                        discourse_rel = self.calc_discourse_relevance(f, session)
                        generated_replicas.append((f,
                                                   phrase_sim * discourse_rel * base_weight,
                                                   'generate_with_common_phrases(1)'))


        # Выбираем ближайший факт
        facts0 = bot.facts.enumerate_facts(interlocutor)
        facts0 = [fact for fact in facts0 if fact[0].lower() != phrase]
        facts = []
        for fact0 in facts0:
            words2 = self.text_utils.tokenize(fact0[0])
            stem_hits = sum(same_stem2(word, key_stems) for word in words2)
            if stem_hits >= 1:
                facts.append(fact0)

        if len(facts) > 0:
            sim_facts = self.jsyndet.get_most_similar(phrase, facts, self.text_utils,
                                                      self.word_embeddings, nb_results=1)
            for fact, fact_sim in [sim_facts]:
                if fact_sim > 0.20 and fact.lower() != phrase:
                    if not self.bot_replica_already_uttered(bot, session, fact):
                        # Среди фактов не может быть вопросов, поэтому не проверяем на знание ответа.
                        discourse_rel = self.calc_discourse_relevance(fact, session)
                        generated_replicas.append(
                            (fact, fact_sim * discourse_rel * base_weight, 'generate_with_common_phrases(2)'))

        return generated_replicas

    def apply_insteadof_rule(self, bot, session, interlocutor, interpreted_phrase):
        if bot.has_scripting():
            external_rule_applied = bot.get_scripting().apply_insteadof_rule(bot, session, interlocutor, interpreted_phrase)
            if external_rule_applied:
                return InsteadofRuleResult.GetTrue(True)

            for rule in bot.get_scripting().get_insteadof_rules():
                if rule.check_condition(interpreted_phrase, self):
                    replica_is_generated = rule.do_action(bot, session, interlocutor, interpreted_phrase)
                    return InsteadofRuleResult.GetTrue(replica_is_generated)

        return InsteadofRuleResult.GetFalse()

    def generate_smalltalk_replica(self, bot, session, interlocutor):
        generated_replicas = []  # список кортежей (подобранная_реплика_бота, вес_реплики)

        if bot.enable_smalltalk and bot.has_scripting():
            # подбираем подходящую реплику в ответ на не-вопрос собеседника (обычно это
            # ответ на наш вопрос, заданный ранее).
            smalltalk_rules = bot.get_scripting().enumerate_smalltalk_rules()

            interlocutor_phrases = session.get_interlocutor_phrases(questions=True, assertions=True)
            for phrase, timegap in interlocutor_phrases[:1]:  # 05.06.2019 берем одну последнюю фразу
                best_premise, best_rel = self.synonymy_detector.get_most_similar(phrase,
                                                                                 [(item.get_condition_text(), -1, -1)
                                                                                  for item in smalltalk_rules],
                                                                                 self.text_utils,
                                                                                 self.word_embeddings)
                time_decay = math.exp(-timegap)  # штрафуем фразы, найденные для более старых реплик

                if best_rel > 0.7:
                    for item in smalltalk_rules:
                        if item.get_condition_text() == best_premise:

                            # Используем это правило для генерации реплики.
                            # Правило может быть простым, с явно указанной фразой, либо
                            # содержать набор шаблонов генерации.

                            if item.is_generator():
                                # Используем скомпилированную грамматику для генерации фраз..
                                words = phrase.split()
                                all_generated_phrases = item.compiled_grammar.generate(words,
                                                                                       self.text_utils.known_words)
                                if len(all_generated_phrases) > 0:
                                    # Уберем вопросы, которые мы уже задавали, оставим top
                                    top = sorted(all_generated_phrases, key=lambda z: -z.get_rank())[:50]
                                    top = filter(lambda z: session.count_bot_phrase(z.get_str()) == 0, top)

                                    # Выберем рандомно одну из фраз
                                    px = [z.get_rank() for z in top]
                                    sum_p = sum(px)
                                    px = [p / sum_p for p in px]
                                    best = np.random.choice(top, 1, p=px)[0]
                                    replica = best.get_str()

                                    discourse_rel = self.calc_discourse_relevance(replica, session)

                                    if not self.bot_replica_already_uttered(bot, session, replica):
                                        # проверить, если f является репликой-ответом: знает
                                        # ли бот ответ на этот вопрос.
                                        good_replica = True
                                        if replica[-1] == u'?':
                                            if self.does_bot_know_answer(replica, bot, session, interlocutor):
                                                good_replica = False

                                        if good_replica:
                                            generated_replicas.append((replica,
                                                                       best.get_rank() * discourse_rel * time_decay,
                                                                       'assertion(1)'))

                            else:
                                # Текст формируемой реплики указан буквально.
                                # Следует учесть, что ответные реплики в SmalltalkReplicas могут быть ненормализованы,
                                # поэтому их следует сначала нормализовать.
                                for replica in item.answers:
                                    if not self.bot_replica_already_uttered(bot, session, replica):
                                        # проверить, если f является репликой-ответом: знает
                                        # ли бот ответ на этот вопрос.
                                        good_replica = True
                                        if replica[-1] == u'?':
                                            if self.does_bot_know_answer(replica, bot, session, interlocutor):
                                                good_replica = False

                                        if good_replica:
                                            discourse_rel = self.calc_discourse_relevance(replica, session)
                                            generated_replicas.append((replica,
                                                                       discourse_rel * time_decay,
                                                                       'assertion(2)'))

                            break
                else:
                    # Проверяем smalltalk-правила, использующие intent фразы
                    intent_rule_applied = False
                    last_interlocutor_utterance = session.get_last_interlocutor_utterance()
                    for item in bot.get_scripting().enumerate_smalltalk_intent_rules():
                        #if item.condition_text == interpreted_phrase.intent:
                        if item.get_condition_text == last_interlocutor_utterance.intent:
                            intent_rule_applied = True
                            if item.is_generator():
                                # Используем скомпилированную грамматику для генерации фраз..
                                words = phrase.split()
                                all_generated_phrases = item.compiled_grammar.generate(words,
                                                                                       self.text_utils.known_words)
                                if len(all_generated_phrases) > 0:
                                    # Уберем вопросы, которые мы уже задавали, оставим top
                                    top = sorted(all_generated_phrases, key=lambda z: -z.get_rank())[:50]
                                    top = filter(lambda z: session.count_bot_phrase(z.get_str()) == 0, top)

                                    # Выберем рандомно одну из фраз
                                    px = [z.get_rank() for z in top]
                                    sum_p = sum(px)
                                    px = [p / sum_p for p in px]
                                    best = np.random.choice(top, 1, p=px)[0]
                                    replica = best.get_str()
                            else:
                                # Текст реплики задан явно:
                                replica = item.pick_random_answer()

                            discourse_rel = self.calc_discourse_relevance(replica, session)

                            if not self.bot_replica_already_uttered(bot, session, replica):
                                # проверить, если f является репликой-ответом: знает
                                # ли бот ответ на этот вопрос.
                                good_replica = True
                                if replica[-1] == u'?':
                                    if self.does_bot_know_answer(replica, bot, session, interlocutor):
                                        good_replica = False

                                if good_replica:
                                    generated_replicas.append(
                                        (replica, best.get_rank() * discourse_rel * time_decay, 'assertion(3)'))

                            break

                    if not intent_rule_applied:
                        list2 = self.generate_with_common_phrases(bot, session, interlocutor, phrase, time_decay)
                        generated_replicas.extend(list2)

                        if len(list2) == 0:
                            # Используем генеративную грамматику для получения возможных реплик
                            list3 = self.generate_with_generative_grammar(bot, session, interlocutor, phrase,
                                                                          time_decay)
                            generated_replicas.extend(list3)

            # пробуем найти среди вопросов, которые задавал человек-собеседник недавно,
            # максимально близкие к вопросам в smalltalk базе.
            if False:
                smalltalk_utterances = set()
                for item in smalltalk_phrases:
                    smalltalk_utterances.update(item.answers)

                interlocutor_phrases = session.get_interlocutor_phrases(questions=True, assertions=False)
                for phrase, timegap in interlocutor_phrases:
                    # Ищем ближайшие реплики для данной реплики человека phrase
                    similar_items = self.synonymy_detector.get_most_similar(phrase,
                                                                            [(s, -1, -1) for s in smalltalk_utterances],
                                                                            self.text_utils,
                                                                            self.word_embeddings,
                                                                            nb_results=5
                                                                            )
                    for replica, rel in similar_items:
                        if session.count_bot_phrase(replica) == 0:
                            time_decay = math.exp(-timegap)
                            generated_replicas.append((replica, rel * 0.9 * time_decay, 'debug3'))

        # Теперь среди подобранных реплик бота в generated_replicas выбираем
        # одну, учитывая их вес.
        if len(generated_replicas) > 0:
            replica_px = [z[1] for z in generated_replicas]
            replicas = list(map(operator.itemgetter(0), generated_replicas))
            sum_p = sum(replica_px)  # +1e-7
            replica_px = [p / sum_p for p in replica_px]
            replica = np.random.choice(replicas, p=replica_px)
            return replica

        return None

    def push_phrase(self, bot, interlocutor, phrase, internal_issuer=False, force_question_answering=False):
        self.logger.info(u'push_phrase interlocutor="%s" phrase="%s"', interlocutor, phrase)
        question = self.text_utils.canonize_text(phrase)
        if question == u'#traceon':
            self.trace_enabled = True
            return
        elif question == u'#traceoff':
            self.trace_enabled = False
            return
        elif question == u'#facts':
            for fact, person, fact_id in bot.facts.enumerate_facts(interlocutor):
                print(u'{}'.format(fact))
            return

        session = self.get_session(bot, interlocutor)

        # Выполняем интерпретацию фразы с учетом ранее полученных фраз,
        # так что мы можем раскрыть анафору, подставить в явном виде опущенные составляющие и т.д.,
        # определить, является ли фраза вопросом, фактом или императивным высказыванием.
        interpreted_phrase = self.interpret_phrase(bot, session, question, internal_issuer)

        if force_question_answering:
            # В случае, если наш бот должен считать все входные фразы вопросами,
            # на которые он должен отвечать.
            interpreted_phrase.set_modality(ModalityDetector.question, interpreted_phrase.person)

        # Утверждения для 2го лица, то есть относящиеся к профилю чатбота, будем
        # рассматривать как вопросы. Таким образом, запрещаем прямой вербальный
        # доступ к профилю чатбота на запись.
        is_question2 = interpreted_phrase.is_assertion and interpreted_phrase.person == 2

        # Интерпретация фраз и в общем случае реакция на них зависит и от истории
        # общения, поэтому результат интерпретации сразу добавляем в историю.
        session.add_phrase_to_history(interpreted_phrase)

        if interpreted_phrase.is_imperative:
            self.logger.debug(u'Processing as imperative: "%s"', interpreted_phrase.interpretation)
            # Обработка приказов (императивов).
            order_processed = self.process_order(bot, session, interlocutor, interpreted_phrase)
            if not order_processed:
                # Сообщим, что не знаем как обработать приказ.
                self.premise_not_found_model.order_not_understood(phrase, bot, self.text_utils, self.word_embeddings)
                order_processed = True
        elif interpreted_phrase.is_question or is_question2:
            self.logger.debug(u'Processing as question: "%s"', interpreted_phrase.interpretation)
            # Обрабатываем вопрос собеседника (либо результат трансляции императива).
            answers = self.build_answers(session, bot, interlocutor, interpreted_phrase)
            for answer in answers:
                self.say(session, answer)

            # Возможно, кроме ответа на вопрос, надо выдать еще какую-то реплику.
            # Например, для смены темы разговора.
            replica_generated = False
            if len(answers) > 0 and bot.has_scripting():
                additional_speech = bot.scripting.generate_after_answer(bot,
                                                                        self,
                                                                        interlocutor,
                                                                        interpreted_phrase,
                                                                        answers[-1])
                if additional_speech is not None:
                    self.say(session, additional_speech)
                    replica_generated = True

            if not replica_generated:
                replica = self.generate_smalltalk_replica(bot, session, interlocutor)
                if replica:
                    self.say(session, replica)
                    replica_generated = True

        else:
            self.logger.debug(u'Processing as assertion: "%s"', interpreted_phrase.interpretation)

            # Обработка прочих фраз. Обычно это просто утверждения (новые факты, болтовня).
            # Пробуем применить общие правила, которые опираются в том числе на
            # intent реплики или ее текст.
            #input_processed = bot.apply_rule(session, interlocutor, interpreted_phrase)
            insteadof_rule_result = self.apply_insteadof_rule(bot, session, interlocutor, interpreted_phrase)
            input_processed = insteadof_rule_result.applied

            # TODO: в принципе возможны два варианты последствий срабатывания
            # правил. 1) считаем, что правило полностью выполнило все действия для
            # утверждения, в том числе сохранило в базе знаний новый факт, если это
            # необходимо. 2) полагаем, что правило что-то сделало, но факт в базу мы должны
            # добавить сами.
            # Возможно, надо явно задавать в правилах эти особенности (INSTEAD-OF или BEFORE)
            # Пока считаем, что правило сделало все, что требовалось.

            answer_generated = False
            answer = None

            if not input_processed:
                # Утверждение добавляем как факт в базу знаний, в раздел для
                # текущего собеседника.
                # TODO: факты касательно третьих лиц надо вносить в общий раздел базы, а не
                # для текущего собеседника.
                fact_person = '3'
                fact = interpreted_phrase.interpretation
                if self.trace_enabled:
                    self.logger.info(u'Adding "%s" to knowledge base', fact)
                bot.facts.store_new_fact(interlocutor, (fact, fact_person, '--from dialogue--'))

            # Теперь генерация реплики для случая, когда реплика собеседника - не-вопрос.
            # 13.07.2019 если применено INSTEADOF-правило, но оно не сгенерировало никакую ответную реплику,
            # то есть резон сказать что-то на базе common_phrases
            if not input_processed or not insteadof_rule_result.replica_is_generated:
                replica = self.generate_smalltalk_replica(bot, session, interlocutor)
                if replica:
                    answer = replica
                    answer_generated = True

            if answer_generated:
                self.say(session, answer)


    def process_order(self, bot, session, interlocutor, interpreted_phrase):
        self.logger.debug(u'Processing order "%s"', interpreted_phrase.interpretation)

        # Пробуем применить общие правила, которые опираются в том числе на
        # intent реплики или ее текст.
        order_processed = self.apply_insteadof_rule(bot, session, interlocutor, interpreted_phrase)
        if order_processed:
            return True
        else:
            return bot.process_order(session, interlocutor, interpreted_phrase)

    def apply_rule(self, bot, session, interpreted_phrase):
        return bot.apply_rule(session, interpreted_phrase)

    def premise_not_found(self, phrase, bot, text_utils, word_embeddings):
        return self.premise_not_found_model.generate_answer(phrase, bot, text_utils, word_embeddings)

    def build_answers0(self, session, bot, interlocutor, interpreted_phrase):
        if self.trace_enabled:
            self.logger.debug(u'Question to process="%s"', interpreted_phrase.interpretation)

        # Проверяем базу FAQ, вдруг там есть развернутый ответ на вопрос.
        best_faq_answer = None
        best_faq_rel = 0.0
        best_faq_question = None
        if bot.faq:
            best_faq_answer, best_faq_rel, best_faq_question = bot.faq.get_most_similar(interpreted_phrase.interpretation,
                                                                                        self.synonymy_detector,
                                                                                        self.word_embeddings,
                                                                                        self.text_utils)

        answers = []
        answer_rels = []
        best_rels = None

        # Нужна ли предпосылка, чтобы ответить на вопрос?
        # Используем модель, которая вернет вероятность того, что
        # пустой список предпосылок достаточен.
        p_enough = self.enough_premises.is_enough(premise_str_list=[],
                                                  question_str=interpreted_phrase.interpretation,
                                                  text_utils=self.text_utils,
                                                  word_embeddings=self.word_embeddings)
        if p_enough > 0.5:
            # Единственный ответ можно построить без предпосылки, например для вопроса "Сколько будет 2 плюс 2?"
            answers, answer_rels = self.answer_builder.build_answer_text([u''], [1.0],
                                                                         interpreted_phrase.interpretation,
                                                                         self.text_utils,
                                                                         self.word_embeddings)
            if len(answers) != 1:
                self.logger.debug(u'Exactly 1 answer is expected for question={}, got {}'.format(interpreted_phrase.interpretation, len(answers)))

            best_rels = answer_rels
        else:
            # определяем наиболее релевантную предпосылку
            memory_phrases = list(bot.facts.enumerate_facts(interlocutor))

            best_premises, best_rels = self.relevancy_detector.get_most_relevant(interpreted_phrase.interpretation,
                                                                                 memory_phrases,
                                                                                 self.text_utils,
                                                                                 self.word_embeddings,
                                                                                 nb_results=3)
            if self.trace_enabled:
                if best_rels[0] >= self.min_premise_relevancy:
                    self.logger.info(u'Best premise is "%s" with relevancy=%f', best_premises[0], best_rels[0])

            if len(answers) == 0:
                if bot.premise_is_answer:
                    if best_rels[0] >= self.min_premise_relevancy:
                        # В качестве ответа используется весь текст найденной предпосылки.
                        answers = [best_premises[:1]]
                        answer_rels = [best_rels[:1]]
                else:
                    premises2 = []
                    premise_rels2 = []

                    # 30.11.2018 будем использовать только 1 предпосылку и генерировать 1 ответ
                    if True:
                        if best_rels[0] >= self.min_premise_relevancy:
                            premises2 = [best_premises[:1]]
                            premise_rels2 = best_rels[:1]
                    else:
                        max_rel = max(best_rels)
                        for premise, rel in zip(best_premises[:1], best_rels[:1]):
                            if rel >= self.min_premise_relevancy and rel >= 0.4 * max_rel:
                                premises2.append([premise])
                                premise_rels2.append(rel)

                    if len(premises2) > 0:
                        # генерация ответа на основе выбранной предпосылки.
                        answers, answer_rels = self.answer_builder.build_answer_text(premises2, premise_rels2,
                                                                                     interpreted_phrase.interpretation,
                                                                                     self.text_utils,
                                                                                     self.word_embeddings)

        if len(best_rels) == 0 or (best_faq_rel > best_rels[0] and best_faq_rel > self.min_faq_relevancy):
            # Если FAQ выдал более достоверный ответ, чем генератор ответа, или если
            # генератор ответа вообще ничего не выдал (в базе фактов пусто), то берем
            # тест ответа из FAQ.
            answers = [best_faq_answer]
            answer_rels = [best_faq_rel]
            self.logger.info(u'FAQ entry provides nearest question="%s" with rel=%e', best_faq_question, best_faq_rel)

        if len(answers) == 0:
            # Не удалось найти предпосылку для формирования ответа.

            # Попробуем обработать вопрос правилами.
            res = self.apply_insteadof_rule(bot, session, interlocutor, interpreted_phrase)
            if not res.applied:
                # Правила не сработали, значит выдаем реплику "Информации нет"
                answer = self.premise_not_found.generate_answer(interpreted_phrase.interpretation,
                                                                bot,
                                                                self.text_utils,
                                                                self.word_embeddings)
                answers.append(answer)
                answer_rels.append(1.0)

        return answers, answer_rels

    def build_answers(self, session, bot, interlocutor, interpreted_phrase):
        answers, answer_confidenses = self.build_answers0(session, bot, interlocutor, interpreted_phrase)
        if len(answer_confidenses) == 0 or max(answer_confidenses) < self.min_premise_relevancy:
            # тут нужен алгоритм генерации ответа в условиях, когда
            # у бота нет нужных фактов. Это может быть как ответ "не знаю",
            # так и вариант "нет" для определенных категорий вопросов.
            if False:  #bot.has_scripting():
                answer = bot.scripting.buid_answer(self, interlocutor, interpreted_phrase)
                answers = [answer]

        return answers

    def pop_phrase(self, bot, interlocutor):
        session = self.get_session(bot, interlocutor)
        return session.extract_from_buffer()

    def get_session(self, bot, interlocutor):
        return self.session_factory.get_session(bot, interlocutor)

    def get_synonymy_detector(self):
        return self.synonymy_detector

    def get_text_utils(self):
        return self.text_utils

    def get_word_embeddings(self):
        return self.word_embeddings