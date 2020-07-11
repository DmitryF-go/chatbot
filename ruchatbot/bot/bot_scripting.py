# -*- coding: utf-8 -*-

import random
import logging
import yaml
import io
import pickle
import os

from ruchatbot.bot.smalltalk_rules import SmalltalkRules
from ruchatbot.generative_grammar.generative_grammar_engine import GenerativeGrammarEngine
from ruchatbot.bot.comprehension_table import ComprehensionTable
from ruchatbot.bot.scripting_rule import ScriptingRule
from ruchatbot.bot.verbal_form import VerbalForm
from ruchatbot.bot.scenario import Scenario
from ruchatbot.utils.constant_replacer import replace_constant


class StoryRules:
    def __init__(self):
        self.keyphrase_rules = []  # список пар (фраза_бота, скомпилированное_правило)
        self.keyphrases3 = []
        self.keyphrase2_2_rules = dict()
        self.keyphrases2 = []
        self.keyphrase3_2_rules = dict()

    def add_rule3(self, key_phrase, rule):
        self.keyphrase_rules.append((key_phrase, rule))
        self.keyphrases3.append((key_phrase, -1, -1))
        if key_phrase in self.keyphrase3_2_rules:
            self.keyphrase3_2_rules[key_phrase].append(rule)
        else:
            self.keyphrase3_2_rules[key_phrase] = [rule]

    def add_rule2(self, key_phrase, rule):
        self.keyphrase_rules.append((key_phrase, rule))
        self.keyphrases2.append((key_phrase, -1, -1))
        if key_phrase in self.keyphrase2_2_rules:
            self.keyphrase2_2_rules[key_phrase].append(rule)
        else:
            self.keyphrase2_2_rules[key_phrase] = [rule]

    def get_keyphrases3(self):
        return self.keyphrases3

    def get_keyphrases2(self):
        return self.keyphrases2

    def get_rules3_by_keyphrase(self, key_phrase):
        return self.keyphrase3_2_rules[key_phrase]

    def get_rules2_by_keyphrase(self, key_phrase):
        return self.keyphrase2_2_rules[key_phrase]


class BotScripting(object):
    def __init__(self, data_folder):
        self.data_folder = data_folder
        self.greetings = []
        self.goodbyes = []
        self.insteadof_rules = []
        self.comprehension_rules = None
        self.forms = []  # список экземпляров VerbalForm
        self.scenarios = []  # список экземпляров Scenario
        self.smalltalk_rules = SmalltalkRules()
        self.story_rules = StoryRules()

    @staticmethod
    def __get_node_list(node):
        if isinstance(node, list):
            return node
        else:
            return [node]

    def load_story_rules(self, rules_dir, data, compiled_grammars_path, constants, text_utils):
        for rule in data['story_rules']:
            try:
                if 'story_rule' in rule:
                    compiled_rule = ScriptingRule.from_yaml(rule['story_rule'], constants, text_utils)
                    if 'switch' in rule['story_rule']:
                        prev_bot_text = rule['story_rule']['switch']['when']['prev_bot_text']
                        self.story_rules.add_rule3(prev_bot_text, compiled_rule)
                    elif 'if' in rule['story_rule']:
                        if 'raw_text' in rule['story_rule']['if']:
                            human_utterance = rule['story_rule']['if']['raw_text']
                        else:
                            human_utterance = rule['story_rule']['if']['text']

                        self.story_rules.add_rule2(human_utterance, compiled_rule)
                    else:
                        raise NotImplementedError()

                elif 'file' in rule:
                    rules_fpath = os.path.join(rules_dir, rule['file'])
                    with io.open(rules_fpath, 'r', encoding='utf-8') as f:
                        data2 = yaml.safe_load(f)
                        self.load_story_rules(rules_dir, data2, compiled_grammars_path, constants, text_utils)
                else:
                    logging.error('Unknown record "%s" in "story_rules" section', str(rule))
                    raise RuntimeError()
            except Exception as ex:
                logging.error(ex)
                raise ex

    def load_instead_rules(self, rules_dir, data, compiled_grammars_path, constants, text_utils):
        if 'rules' in data:
            for rule in data['rules']:
                try:
                    if 'rule' in rule:
                        rule = ScriptingRule.from_yaml(rule['rule'], constants, text_utils)
                        self.insteadof_rules.append(rule)
                    elif 'file' in rule:
                        rules_fpath = os.path.join(rules_dir, rule['file'])
                        with io.open(rules_fpath, 'r', encoding='utf-8') as f:
                            data2 = yaml.safe_load(f)
                            self.load_instead_rules(rules_dir, data2, compiled_grammars_path, constants, text_utils)
                    else:
                        logging.error('Unknown record "%s" in "rules" section', str(rule))
                        raise RuntimeError()
                except Exception as ex:
                    logging.error(ex)
                    raise ex

    def load_rules(self, yaml_path, compiled_grammars_path, constants, text_utils):
        with io.open(yaml_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
            if 'greeting' in data:
                self.greetings = []
                for s in data['greeting']:
                    self.greetings.append(replace_constant(s, constants, text_utils))

            if 'goodbye' in data:
                self.goodbyes = []
                for s in data['goodbye']:
                    self.goodbyes.append(replace_constant(s, constants, text_utils))

            if 'forms' in data:
                for form_node in data['forms']:
                    form = VerbalForm.from_yaml(form_node['form'], constants, text_utils)
                    self.forms.append(form)

            # Для smalltalk-правил нужны скомпилированные генеративные грамматики.
            smalltalk_rule2grammar = dict()
            with open(compiled_grammars_path, 'rb') as f:
                n_rules = pickle.load(f)
                for _ in range(n_rules):
                    key = pickle.load(f)
                    grammar = GenerativeGrammarEngine.unpickle_from(f)
                    grammar.set_dictionaries(text_utils.gg_dictionaries)
                    smalltalk_rule2grammar[key] = grammar

            if 'scenarios' in data:
                for scenario_node in data['scenarios']:
                    scenario = Scenario.load_yaml(scenario_node['scenario'], smalltalk_rule2grammar, constants, text_utils)
                    self.scenarios.append(scenario)

            if 'story_rules' in data:
                self.load_story_rules(os.path.dirname(yaml_path), data, compiled_grammars_path, constants, text_utils)

            # INSTEAD-OF правила
            if 'rules' in data:
                self.load_instead_rules(os.path.dirname(yaml_path), data, compiled_grammars_path, constants, text_utils)

            if 'smalltalk_rules' in data:
                self.smalltalk_rules.load_yaml(data['smalltalk_rules'], smalltalk_rule2grammar, constants, text_utils)

            self.comprehension_rules = ComprehensionTable()
            self.comprehension_rules.load_yaml_data(data, constants, text_utils)

            self.common_phrases = []
            if 'common_phrases' in data:
                for common_phrase in data['common_phrases']:
                    common_phrase = replace_constant(common_phrase, constants, text_utils)
                    self.common_phrases.append(common_phrase)

    def add_scenario(self, scenario):
        self.scenarios.append(scenario)

    def get_smalltalk_rules(self):
        return self.smalltalk_rules

    def start_conversation(self, chatbot, session):
        # Начало общения с пользователем, для которого загружена сессия session
        # со всей необходимой информацией - история прежних бесед и т.д
        # Выберем одну из типовых фраз в файле smalltalk_opening.txt, вернем ее.
        logging.info(u'BotScripting::start_conversation')
        if len(self.greetings) > 0:
            return random.choice(self.greetings)

        return None

    def generate_after_answer(self, bot, answering_machine, interlocutor, interpreted_phrase, answer):
        # todo: потом вынести реализацию в производный класс, чтобы тут осталась только
        # пустая заглушка метода.

        # language_resources = answering_machine.text_utils.language_resources
        # probe_query_str = language_resources[u'как тебя зовут']
        # probe_query = InterpretedPhrase(probe_query_str)
        # answers, answer_confidenses = answering_machine.build_answers0(bot, interlocutor, probe_query)
        # ask_name = False
        # if len(answers) > 0:
        #     if answer_confidenses[0] < 0.70:
        #         ask_name = True
        # else:
        #     ask_name = True
        #
        # if ask_name:
        #     # имя собеседника неизвестно.
        #     q = language_resources[u'А как тебя зовут?']
        #     nq = answering_machine.get_session(bot, interlocutor).count_bot_phrase(q)
        #     if nq < 3:  # Не будем спрашивать более 2х раз.
        #         return q
        return None

    def get_insteadof_rules(self):
        return self.insteadof_rules

    def get_story_rules(self):
        return self.story_rules
