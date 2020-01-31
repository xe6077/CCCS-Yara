import plyara.utils
from pathlib import Path
import collections
from enum import Enum

# for loading the configuration file
import json
import yaml

# for calculate_rule_hash function
import hashlib
import re

# for the UUID
import uuid
import baseconv

# for version checking function
from packaging import version

# for date checking function
import datetime

# for querying the MITRE ATT&CK data
from stix2 import FileSystemSource
from stix2 import Filter
from cfg.filter_casefold import FilterCasefold

# set current working directory
SCRIPT_LOCATION = Path(__file__).resolve().parent
MITRE_STIX_DATA_PATH= SCRIPT_LOCATION.parent / 'cti/enterprise-attack'
VALIDATOR_YAML_PATH = SCRIPT_LOCATION.parent / 'CCCS_Yara_values.yml'
CONFIGURATION_YAML_PATH = SCRIPT_LOCATION.parent / 'CCCS_Yara.yml'

# constants to deal with various required string comparisons
SCOPES = 'scopes'
GLOBAL = '^global$'
ASCII = 'ascii'
MITRE_GROUP_NAME = 'name'
UNVALIDATED_REGEX = "^.*$"
BASE62_REGEX = "^[0-9a-zA-z]+$"
CATEGORY_TYPE_REGEX = '^[A-Z\- 0-9_]*$'
UNIVERSAL_REGEX = '^[^a-z]*$'
OPENSOURCE_REGEX = '^OPENSOURCE$'

# constants to store the string tag used to reference to particular important tags
METADATA = 'metadata'
REPORT = 'report'
HASH = 'hash'
ACTOR = 'actor'
AUTHOR = 'author'

# potential values of TagAttributes.optional variable
class TagOpt(Enum):
    REQ_PROVIDED = 'req_provided'
    REQ_OPTIONAL = 'req_optional'
    OPT_OPTIONAL = 'opt_optional'

"""
RUN THE VALIDATOR BY CALLING THIS FUNCTION IF YOU ARE NOT USING THE cccs_yara.py script
"""
def run_yara_validator(yara_file):
    """
    This is the base function that should be called to validate a rule. It will take as an argument the file path,
        create a YaraValidator object, parse that file with plyara and pass that parsed object and the string representation
        of the yara file to YaraValidator.valadation

        NOTE the current function assumes one rule per file and will only process the first rule found.
    :param yara_file:
    :return:
    """
    validator = YaraValidator()

    parser = plyara.Plyara()
    yara_rule_file = open(yara_file, encoding='utf-8')
    yara_rule_file_string = yara_rule_file.read()
    rule0 = parser.parse_string(yara_rule_file_string)[0]
    yara_rule_file.close()
    rule_return = validator.validation(rule0, yara_rule_file_string)

    return rule_return

class YaraValidatorReturn:
    """
    YaraValidatorReturn class used to pass the validity of the processed rules, what metadata tags have issues if not valid,
        a string representation of the original rule and if the rule is valid a string representation of the valid rule
        with all the created metadata tags, etc.
    """
    def __init__(self, original_rule):
        # Overall rule validity flag
        self.rule_validity = True
        # each possible metadata tag
        self.metadata_tags = collections.OrderedDict()
        # Overall warning flag
        self.rule_warnings = False
        # collection of all the warnings
        self.warnings = collections.OrderedDict()
        # the original_rule
        self.rule_to_validate = original_rule
        # set
        self.validated_rule = None

    def update_validity(self, rule_validity, metadata_tag, message):
        if self.rule_validity:
           self.rule_validity = rule_validity

        self.metadata_tags[metadata_tag] = message

    def update_warning(self, rule_warning, warning_tag, message):
        if not self.rule_warnings:
            self.rule_warnings = rule_warning

        self.warnings[warning_tag] = message

    def __build_return_string(self, collection):
        return_string = ""
        for index, tag in enumerate(collection):
            if index > 0:
                return_string = return_string + "\n"
            return_string = return_string + tag + ": " + collection[tag]

        return return_string

    def __build_return_string_cmlt(self, collection):
        return_string = ""
        for index, tag in enumerate(collection):
            if index > 0:
                return_string = return_string + "\n"
            return_string = return_string + "{indent:>9}{tag:30} {collection}".format(indent="- ", tag=tag + ":", collection=collection[tag])

        return return_string

    def return_errors(self):
        error_string = ""
        if not self.rule_validity:
            error_string = self.__build_return_string(self.metadata_tags)

        return error_string

    def return_errors_for_cmlt(self):
        error_string = ""
        if not self.rule_validity:
            error_string = self.__build_return_string_cmlt(self.metadata_tags)

        return error_string

    def return_warnings(self):
        warning_string = ""
        if self.rule_warnings:
            warning_string = self.__build_return_string(self.warnings)

        return warning_string

    def return_warnings_for_cmlt(self):
        warning_string = ""
        if self.rule_warnings:
            warning_string = self.__build_return_string_cmlt(self.warnings)

        return warning_string

    def return_original_rule(self):
        return self.rule_to_validate

    def return_validated_rule(self):
        return self.validated_rule

    def set_validated_rule(self, valid_rule):
        self.validated_rule = valid_rule

    def __find_meta_start_end(self, rule_to_process):
        """
        A string representation of a yara rule is passed into this function, it performs the splitlines() function,
            searches for the start and the end indexes of the meta section of the first yara rule.
        :param rule_to_process: The Rule to be processed
        :return: a tuple of the array of lines for the rule processed, the start of meta index and the end of meta index
        """
        rule_to_process_lines = rule_to_process.splitlines()
        rule_start = 0
        rule_end = 0
        meta_regex = "^\s*meta\s*:\s*$"
        next_section = "^\s*strings\s*:\s*$"

        for index, line in enumerate(rule_to_process_lines):
            if rule_start > 0:
                if re.match(next_section, line):
                    rule_end = index
                    break
            else:
                if re.match(meta_regex, line):
                    rule_start = index

        return rule_to_process_lines, rule_start, rule_end

    def rebuild_rule(self):
        """
        Rebuilds the rule if it is valid and as long as there are any changes. This was created to maintain
            any comments outside of the metadata section
        :return: No return
        """
        if self.validated_rule[-1] == '\n':
            self.validated_rule = self.validated_rule[:-1]

        if self.rule_to_validate is None or self.validated_rule is None:
            exit()
        elif self.rule_to_validate == self.validated_rule:
            return

        yara_valid_lines, yara_valid_meta_start, yara_valid_meta_end = self.__find_meta_start_end(self.rule_to_validate)
        yara_cccs_lines, yara_cccs_meta_start, yara_cccs_meta_end = self.__find_meta_start_end(self.validated_rule)

        if yara_valid_meta_start != 0 and yara_valid_meta_end != 0 and yara_cccs_meta_start != 0 and yara_cccs_meta_end != 0:
            yara_new_file = yara_valid_lines[0:yara_valid_meta_start] + yara_cccs_lines[yara_cccs_meta_start:yara_cccs_meta_end] + yara_valid_lines[yara_valid_meta_end:]
            yara_new_file = "\n".join(yara_new_file)
            if self.rule_to_validate != yara_new_file:
                self.validated_rule = yara_new_file

class TagAttributes:
    """
    TagAttributes class is used to populate the YaraValidator.required_fields dict and stores values such as the type of method used to
        validate the given metadata tag, regex expression or funcion name used to verify, the optionality of the metadata tag,
        the max count of the metadata tag and the position of the matching Positional object in the YaraValidator.required_fields_index
    """
    function = None
    argument = None
    optional = None
    max_count = None
    position = None
    found = False
    valid = False

    def __init__(self, tag_validator, tag_optional, tag_max_count, tag_position, tag_argument):
        self.function = tag_validator
        self.argument = tag_argument
        self.optional = tag_optional
        self.max_count = tag_max_count
        self.position = tag_position

    def attributefound(self):
        self.found = True

    def attributevalid(self):
        self.valid = True

    def attributeinvalid(self):
        self.valid = False

    def attributereset(self):
        self.found = False
        self.valid = False

class Positional:
    """
    Positional class used to create positional objects for the YaraValidator.required_fields_index. This allows for tracking the count
        of each metadata tag found and the relative start and end positions given the canonical order
    """
    def __init__(self, position_index, position_count = 0):
        self.starting_index = position_index
        self.count = position_count
        self.current_offset = 0

    def set_values(self, position_index, position_count = 0):
        self.starting_index = position_index
        self.count = position_count
        self.current_offset = 0

    def increment_count(self):
        self.count = self.count + 1

    def increment_offset(self):
        self.current_offset = self.current_offset + 1
        if self.current_offset >= self.count:
            self.current_offset = 0

    def reindex(self, previous_values):
        self.starting_index = previous_values[0] + previous_values[1]

    def current_values(self):
        return self.starting_index, self.count

    def index(self):
        return self.starting_index + self.current_offset

class YaraValidator:
    """
    Class for YaraValidator that does most of the work for validating yara rules to the CCCS Yara Standard
    """
    previous_position_values = None

    def reindex_metadata_tags(self):
        """
        Reindex the starting index of the positional objects contained in self.required_fields_index. This is so that
            the canonical order is maintained relative to optional and multiple instances of some metadata
        :return: none, it works on the self.required_fields_index and makes changes to that
        """
        previous_position_values = None

        for position_index, position in enumerate(self.required_fields_index):
            if position_index > 0:
                position.reindex(previous_position_values)

            previous_position_values = position.current_values()

    def resort_metadata_tags(self, rule_to_sort):
        """
        Resorts the array of metadata tags for valid rules into the canonical order
        :param rule_to_sort: the plyara parsed rule that is being validated
        :return: No return, it simply replaces the rules metadata array with the sorted array
        """
        metadata_tags = rule_to_sort[METADATA]
        correct_order = [None] * len(metadata_tags)
        tracking_added = 0
        tracking_left = 0
        for tag in list(metadata_tags):
            if len(tag.keys()) == 1:
                key = list(tag.keys())[0]
                value = list(tag.values())[0]

                if key in self.required_fields:
                    positional = self.required_fields_index[self.required_fields[key].position]
                    correct_order[positional.index()] = metadata_tags.pop(tracking_left)
                    positional.increment_offset()
                    tracking_added = tracking_added + 1
                elif key in self.required_fields_children:
                    positional = self.required_fields_index[self.required_fields_children[key].position]
                    correct_order[positional.index()] = metadata_tags.pop(tracking_left)
                    positional.increment_offset()
                    tracking_added = tracking_added + 1
                else:
                    tracking_left = tracking_left + 1
            else:
                tracking_left = tracking_left + 1

        # takes all unrecognized or multivalue metadata and appends them to the end of the array of metadata
        for tag in list(metadata_tags):
            correct_order[tracking_added] = metadata_tags.pop(0)
            tracking_added = tracking_added + 1

        rule_to_sort[METADATA] = correct_order

    def process_key(self, key, fields, rule_processing_key, tag_index):
        """
        The primary function that determines how to treat a specific metadata tag for validation, it will either call
            the function or perform the regex comparison
        :param key: the name of the metadata tag that is being processed
        :param fields: the dictonary of metadata tags to check against this can differ depending on where validation is in the process
        :param rule_processing_key: the plyara parsed rule that is being validated
        :param tag_index: used to reference what the array index of the key being processed is
        :return:
        """
        if not fields[key].function(rule_processing_key, tag_index, key):
            rule_response = "Field has Invalid Value:\t" + str(rule_processing_key[METADATA][tag_index][key])
            return False, rule_response
        return True, ""

    def is_ascii(self, rule_string):
        """
        Takes the string of the rule and parses it to check if there are only ascii characters present.
        :param rule_string: the string representation of the yara rule
        :return: true if there are only ascii characters in the string
        """
        return len(rule_string) == len(rule_string.encode())

    def validation(self, rule_to_validate, rule_to_validate_string):
        """
        Called to validate a yara rule. This is the primary function.
        :param rule_to_validate: the plyara parsed rule that is being validated
        :param rule_to_validate_string: the string representation of the yara rule to verify, this is passed to the YaraValidatorReturn object for use later
        :return: the valid object of the YaraValidatorReturn class
        """
        valid = YaraValidatorReturn(rule_to_validate_string)

        if not METADATA in rule_to_validate:
            valid.update_validity(False, METADATA, "No Metadata Present")
            return valid

        if not self.is_ascii(rule_to_validate_string):
            valid.update_validity(False, ASCII, "There are Non-ASCII Characters Present in the Rule.")
            return valid

        if SCOPES in rule_to_validate:
            for scope in rule_to_validate[SCOPES]:
                if re.match(GLOBAL, scope):
                    valid.update_validity(False, SCOPES, "This is a Global Rule.")
                    return valid

        metadata_tags = rule_to_validate[METADATA]
        index_of_empty_tags = []
        tags_not_initially_found = []
        for tag_index, tag in enumerate(metadata_tags):
            if len(tag.keys()) == 1:
                key = list(tag.keys())[0]
                value = list(tag.values())[0]

                if value == '':
                    index_of_empty_tags.append(tag_index)
                elif key in self.required_fields:
                    validity, rule_response = self.process_key(key, self.required_fields, rule_to_validate, tag_index)
                    if not validity:
                        valid.update_validity(validity, key, rule_response)
                elif str(key).lower() in self.required_fields:
                    valid.update_warning(True, key, "Warning, this metadata tag would be validated if it were lowercase.")
                else:
                    tag_index_and_tag = {key: tag_index}
                    tags_not_initially_found.append(tag_index_and_tag)

        tags_not_initially_found.reverse()
        for tag_to_check in tags_not_initially_found:
            if len(tag_to_check.keys()) == 1:
                key_to_match = list(tag_to_check.keys())[0]
                metadata_tag_index = list(tag_to_check.values())[0]

                tag = rule_to_validate[METADATA][metadata_tag_index]
                if len(tag.keys()) == 1:
                    key = list(tag.keys())[0]
                    value = list(tag.values())[0]

                    if key in self.required_fields_children:
                        validity, rule_response = self.process_key(key, self.required_fields_children, rule_to_validate, metadata_tag_index)
                        if not validity:
                            valid.update_validity(validity, key, rule_response)

        for empty_tag in sorted(index_of_empty_tags, reverse=True):
            if list(rule_to_validate[METADATA][empty_tag].values())[0] == '':
                metadata_tags.pop(empty_tag)

        self.generate_required_optional_tags(rule_to_validate)

        for key, value in self.required_fields.items():
            if not value.found and not str(key).upper() in self.category_types:
                if value.optional == TagOpt.REQ_PROVIDED:
                    valid.update_validity(False, key, "Missing Required Metadata Tag")
                #else:
                    #valid.update_warning(True, key, "Optional Field Not Provided")
            else:
                if self.required_fields_index[value.position].count > value.max_count and value.max_count != -1:
                    valid.update_validity(False, key, "Too Many Instances of Metadata Tag.")

        if valid.rule_validity:
            self.reindex_metadata_tags()
            self.resort_metadata_tags(rule_to_validate)
            valid.set_validated_rule(plyara.utils.rebuild_yara_rule(rule_to_validate))
            valid.rebuild_rule()

        self.warning_check(rule_to_validate, valid)

        return valid

    def valid_metadata_index(self, rule, index):
        """
        Ensures that the index will not return an out of bounds error
        :param rule: the plyara parsed rule that is being validated
        :param index: the potential index
        :return: True if the potential index will not be out of bounds and false otherwise
        """
        count_of_tags = len(rule[METADATA])
        if index >= count_of_tags:
            return False
        else:
            return True

    def regex_match_string_names_for_values(self, string_name_preface, string_name_expression, string_substitutions):
        """
        Looks to replace yara string references in conditions such as $a*. The function looks to match all matching
            string names and compile a completed list of those string values
        :param string_name_preface: Can be one of "$", "!", "#", or "@"
        :param string_name_expression: The string name expression that will be converted into a regex pattern
        :param string_substitutions: the dict of all string substitutions and values
        :return: the completed list of string values whose string names match the expression
        """
        string_name, string_suffix = string_name_expression[:-1], string_name_expression[-1:]
        string_name_regex = "^\\" + string_name + "." + string_suffix + "$"
        string_value_matches = []
        for key in string_substitutions.keys():
            if re.fullmatch(string_name_regex, key):
                string_value_matches.append(string_name_preface+string_substitutions[key])

        return string_value_matches

    def resort_stings_add_commas(self, list_of_strings):
        """
        Takes a list of string values and rebuilds it as a sting with comma delimiters so it would look like a hard
            coded yara list of strings
        :param list_of_strings: the list of collected string values
        :return: the sorted list
        """
        list_of_strings.sort()
        count_of_strings = len(list_of_strings)

        for index, string in enumerate(list_of_strings):
            if index + 1 < count_of_strings:
                list_of_strings.insert(index+index+1, ',')

        return list_of_strings

    # This comes from "https://gist.github.com/Neo23x0/577926e34183b4cedd76aa33f6e4dfa3" Cyb3rOps.
    # There have been significant changes to this function to better generate a hash of the strings and conditions
    def calculate_rule_hash(self, rule):
        """
        Calculates a hash over the relevant YARA rule content (string contents, sorted condition)
        Requires a YARA rule object as generated by 'plyara': https://github.com/plyara/plyara
        :param rule: yara rule object
        :return hash: generated hash
        """
        hash_strings = []
        condition_string_prefaces = ("$", "!", "#", "@")
        # dictionary for substitutions
        string_substitutions = {}
        all_strings = []
        # original code used md5
        # m = hashlib.md5()
        m = hashlib.sha3_256()
        # Adding all string contents to the list
        if 'strings' in rule:
            for s in rule['strings']:
                if s['type'] == "byte":
                    # original code just needed to append the converted hex code as a string. We need to create the dictionary entries for substitutions as well
                    # hash_strings.append(re.sub(r'[^a-fA-F\?0-9]+', '', s['value']))
                    byte_code_string = re.sub(r'[^a-fA-F\?0-9]+', '', s['value'])
                    dict_entry = {s['name']: byte_code_string}
                    string_substitutions.update(dict_entry)
                    hash_strings.append(byte_code_string)
                else:
                    # The following line was the only portion of this else statement in the original code
                    # This change takes modifiers into account for string arguments
                    # hash_strings.append(s['value'])
                    string_and_modifiers = []
                    string_and_modifiers.append(s['value'])
                    if 'modifiers' in s:
                        for modifier in s['modifiers']:
                            string_and_modifiers.append(modifier)
                    string_and_modifiers = " ".join(string_and_modifiers)
                    all_strings.append("$"+string_and_modifiers)
                    dict_entry = {s['name']: string_and_modifiers}
                    string_substitutions.update(dict_entry)
                    #hash_strings.append("$"+string_and_modifiers)
        all_strings = self.resort_stings_add_commas(all_strings)
        # Adding the components of the condition to the list (except the variables)
        all_wild_card_1 = "\$\*"
        all_wild_card_2 = "them"
        for e in rule['condition_terms']:
            if re.match(all_wild_card_1, e) or re.match(all_wild_card_2, e):
                hash_strings.extend(all_strings)
            elif e.startswith(condition_string_prefaces):
                if len(e) > 1:
                    string_preface, string_name = e[:1], e[1:]
                    string_name = "$" + string_name
                    if e.endswith("*"):
                        hash_strings.extend(self.resort_stings_add_commas(self.regex_match_string_names_for_values(string_preface, string_name, string_substitutions)))
                        #hash_strings.extend("Pull all the matching strings")
                    else:
                        if string_name in string_substitutions:
                            substituted = string_preface + string_substitutions[string_name]
                            hash_strings.append(substituted)
                        else:
                            hash_strings.append(e)
                else:
                    hash_strings.append(e)
            else:
                hash_strings.append(e)
        # Generate a hash from the sorted contents
        #hash_strings.sort()
        m.update("".join(hash_strings).encode("ascii"))
        return m.hexdigest()

    def valid_fingerprint(self, rule_to_generate_id, tag_index, tag_key):
        """
        Calculates a valid fingerprint for the fingerprint metadata tag and inserts it or replaces the existing value
            of the fingerprint metadata tag.
            Current functionality is not to check the value of an existing fingerprint metadata tag and just overwrite
            it as this is automatically filled out.
        :param rule_to_generate_id: the plyara parsed rule that is being validated
        :param tag_index: used to reference what the array index of the fingerprint metadata tag is
        :param tag_key: the name of the metadata tag that is being processed
        :return: This should return True all the time as there will always be a return from self.calculate_rule_hash
        """
        FINGERPRINT = tag_key
        self.required_fields[FINGERPRINT].attributefound()
        self.required_fields_index[self.required_fields[FINGERPRINT].position].increment_count()

        rule_hash = None
        rule_hash = self.calculate_rule_hash(rule_to_generate_id)
        if rule_hash:
            rule_id = {FINGERPRINT: rule_hash}
            if self.valid_metadata_index(rule_to_generate_id, tag_index):
                if list(rule_to_generate_id[METADATA][tag_index].keys())[0] == FINGERPRINT:
                    rule_to_generate_id[METADATA][tag_index] = rule_id
                    self.required_fields[FINGERPRINT].attributevalid()
                else:
                    rule_to_generate_id[METADATA].insert(tag_index, rule_id)
                    self.required_fields[FINGERPRINT].attributevalid()
            else:
                rule_to_generate_id[METADATA].append(rule_id)
                self.required_fields[FINGERPRINT].attributevalid()

        return self.required_fields[FINGERPRINT].valid

    def validate_uuid(self, uuid_to_check):
        """
        Validates the uuid by checking the base62_uuid matches the potential characters and is of the correct length
        :param uuid_to_check: the value to be
        :return: True if the decoded value of the id is 127 bits in length and False otherwise
        """
        if re.fullmatch(BASE62_REGEX, uuid_to_check):
            return 20 <= len(uuid_to_check) <= 22
        else:
            return False

    def valid_uuid(self, rule_to_generate_uuid, tag_index, tag_key):
        """
        Creates a valid UUID for the id metadata tag and inserts it or verifies an existing id metadata tag
        :param rule_to_generate_uuid: the plyara parsed rule that is being validated
        :param tag_index: used to reference what the array index of the id metadata tag is
        :param tag_key: the name of the metadata tag that is being processed
        :return: True if a the value of the id metadata tag is of the correct size or if a new UUID is generated or
            False if the existing value is not of the correct size
        """
        UUID = tag_key
        self.required_fields[UUID].attributefound()
        self.required_fields_index[self.required_fields[UUID].position].increment_count()

        rule_uuid = {UUID: str(baseconv.base62.encode(uuid.uuid4().int))}
        if self.valid_metadata_index(rule_to_generate_uuid, tag_index):
            if list(rule_to_generate_uuid[METADATA][tag_index].keys())[0] == UUID:
                if self.validate_uuid(list(rule_to_generate_uuid[METADATA][tag_index].values())[0]):
                    self.required_fields[UUID].attributevalid()
                else:
                    self.required_fields[UUID].attributeinvalid()
            else:
                rule_to_generate_uuid[METADATA].insert(tag_index, rule_uuid)
                self.required_fields[UUID].attributevalid()
        else:
            rule_to_generate_uuid[METADATA].append(rule_uuid)
            self.required_fields[UUID].attributevalid()

        return self.required_fields[UUID].valid

    def valid_regex(self, rule_to_validate, tag_index, tag_key):
        """
        Validates the metadata tag using provided regex expression
        :param rule_to_validate: the plyara parsed rule that is being validated
        :param tag_index: used to reference what the array index of the id metadata tag is
        :param tag_key: the name of the metadata tag that is being processed
        :return: True if the value of the metadata tag follows the regex expression or
            False if the value is does not match the expression
        """
        value = list(rule_to_validate[METADATA][tag_index].values())[0]

        self.required_fields[tag_key].attributefound()
        self.required_fields_index[self.required_fields[tag_key].position].increment_count()

        regex_expression = self.required_fields[tag_key].argument.get("regexExpression")

        if re.fullmatch(regex_expression, value):
            self.required_fields[tag_key].attributevalid()
        elif re.fullmatch(regex_expression, str(value).upper()):
            self.required_fields[tag_key].attributevalid()
            rule_to_validate[METADATA][tag_index][tag_key] = str(value).upper()
        else:
            self.required_fields[tag_key].attributeinvalid()
            return False
        return True

    def get_group_from_alias(self, alias):
        """
        Maps any alias to the potential MITRE ATT&CK group name, if the provided name is a known alias.
        :param alias: The alias to check
        :return: Either returns the MITRE ATT&CK group name or returns "" if the query returns null
        """
        group_from_alias =  self.fs.query([
            Filter('type', '=', 'intrusion-set'),
            FilterCasefold('aliases', 'casefold', alias)
        ])

        if not group_from_alias:
            return ""

        return group_from_alias[0][MITRE_GROUP_NAME]

    def mitre_group_generator(self, rule_to_generate_group, tag_index, tag_key):
        """
        This will only be looked for if the actor metadata tag has already been processed.
            Current functionality is not to check the value of an existing mitre_group metadata tag and just overwrite
            it as this is automatically filled out. Also if no alias is found it will be removed.
        :param rule_to_generate_group: the plyara parsed rule that is being validated
        :param tag_index: used to reference what the array index of the mitre_group metadata tag is
        :param tag_key: the name of the metadata tag that is being processed
        :return: This should return True all the time as there will always be a return from self.get_group_from_alias
        """
        place_holder = self.required_fields[ACTOR].argument.get("child_place_holder")
        if self.required_fields.get(tag_key):  # if child place holder is passed as tag_key
            MITRE_GROUP = self.required_fields[self.required_fields[tag_key].argument['parent']].argument['child']
        else:
            MITRE_GROUP = tag_key

        mitre_group = str(self.get_group_from_alias(self.mitre_group_alias)).upper()
        rule_group = {MITRE_GROUP: mitre_group}
        if self.valid_metadata_index(rule_to_generate_group, tag_index):
            if list(rule_to_generate_group[METADATA][tag_index].keys())[0] == MITRE_GROUP:
                if mitre_group:
                    rule_to_generate_group[METADATA][tag_index] = rule_group
                    self.required_fields[place_holder].attributefound()
                    self.required_fields[place_holder].attributevalid()
                    self.required_fields_index[self.required_fields[place_holder].position].increment_count()
                else:
                    rule_to_generate_group[METADATA].pop(tag_index)
                    return True
            else:
                if mitre_group:
                    rule_to_generate_group[METADATA].insert(tag_index, rule_group)
                    self.required_fields[place_holder].attributefound()
                    self.required_fields[place_holder].attributevalid()
                    self.required_fields_index[self.required_fields[place_holder].position].increment_count()
                else:
                    return True
        else:
            if mitre_group:
                rule_to_generate_group[METADATA].append(rule_group)
                self.required_fields[place_holder].attributefound()
                self.required_fields[place_holder].attributevalid()
                self.required_fields_index[self.required_fields[place_holder].position].increment_count()
            else:
                return True

        return self.required_fields[place_holder].valid

    def valid_actor(self, rule_to_validate_actor, tag_index, tag_key):
        """
        Validates the actor, makes the actor_type metadata tag required.
            Adds a required metadata tag for mitre_group to hold the a potential alias value.
            Also stores the value of the actor metadata tag in self.mitre_group_alias variable for use with the
            mitre_group_generator function
        :param rule_to_validate_actor: the plyara parsed rule that is being validated
        :param tag_index: used to reference what the array index of the actor metadata tag is
        :param tag_key: the name of the metadata tag that is being processed
        :return: True if the value matches the self.mitre_group_alias_regex and False if it does not
        """
        ACTOR = tag_key
        ACTOR_TYPE = self.required_fields[ACTOR].argument.get("required")
        child_tag = self.required_fields[ACTOR].argument.get("child")
        child_tag_place_holder = self.required_fields[ACTOR].argument.get("child_place_holder")

        self.required_fields[ACTOR].attributefound()
        self.required_fields_index[self.required_fields[ACTOR].position].increment_count()

        # Because there is an actor actor_type becomes required
        self.required_fields[ACTOR_TYPE].optional = TagOpt.REQ_PROVIDED
        metadata = rule_to_validate_actor[METADATA]
        actor_to_check = metadata[tag_index][ACTOR]
        if re.fullmatch(self.mitre_group_alias_regex, actor_to_check):
            self.required_fields[ACTOR].attributevalid()
            add_mitre_group_to_required = {child_tag: self.required_fields[child_tag_place_holder]}
            self.required_fields_children.update(add_mitre_group_to_required)
            self.mitre_group_alias = actor_to_check
        elif re.fullmatch(self.mitre_group_alias_regex, str(actor_to_check).upper()):
            actor_to_check = str(actor_to_check).upper()
            metadata[tag_index][ACTOR] = actor_to_check
            self.required_fields[ACTOR].attributevalid()
            add_mitre_group_to_required = {child_tag: self.required_fields[child_tag_place_holder]}
            self.required_fields_children.update(add_mitre_group_to_required)
            self.mitre_group_alias = actor_to_check
        else:
            self.required_fields[ACTOR].attributeinvalid()

        return self.required_fields[ACTOR].valid

    def get_technique_by_id(self, id_code):
        """
        Used if the id_code is prefaced with T
        :param id_code: The value of the mitre_att metadata tag
        :return: The return of the query of the MITRE ATT&CK database, null if there are no matches
        """
        return self.fs.query([
            Filter('type', '=', 'attack-pattern'),
            Filter('external_references.external_id', '=', id_code)
        ])

    def get_software_by_id(self, id_code):
        """
        Used if the id_code is prefaced with S
        :param id_code: The value of the mitre_att metadata tag
        :return: The return of the query of the MITRE ATT&CK database, null if there are no matches
        """
        malware_return =  self.fs.query([
            Filter('type', '=', 'malware'),
            Filter('external_references.external_id', '=', id_code)
        ])

        tool_return = self.fs.query([
            Filter('type', '=', 'tool'),
            Filter('external_references.external_id', '=', id_code)
        ])

        if malware_return:
            return malware_return
        elif tool_return:
            return tool_return

    def get_tactic_by_id(self, id_code):
        """
        Used if the id_code is prefaced with TA
        :param id_code: The value of the mitre_att metadata tag
        :return: The return of the query of the MITRE ATT&CK database, null if there are no matches
        """
        return self.fs.query([
            Filter('type', '=', 'x-mitre-tactic'),
            Filter('external_references.external_id', '=', id_code)
        ])

    def get_group_by_id(self, id_code):
        """
        Used if the id_code is prefaced with G
        :param id_code: The value of the mitre_att metadata tag
        :return: The return of the query of the MITRE ATT&CK database, null if there are no matches
        """
        return self.fs.query([
            Filter('type', '=', 'intrusion-set'),
            Filter('external_references.external_id', '=', id_code)
        ])

    def get_mitigation_by_id(self, id_code):
        """
        Used if the id_code is prefaced with M
        :param id_code: The value of the mitre_att metadata tag
        :return: The return of the query of the MITRE ATT&CK database, null if there are no matches
        """
        return self.fs.query([
            Filter('type', '=', 'course-of-action'),
            Filter('external_references.external_id', '=', id_code)
        ])

    def get_mitreattck_by_id(self, id_code):
        """
        Used if the id_code is prefaced with an unknown letter. This is about 20x more inefficient then the queries
            that specify types in the filters
            It is used as a catch all in case new MITRE ATT&CK ID Code types are added in the future
        :param id_code: The value of the mitre_att metadata tag
        :return: The return of the query of the MITRE ATT&CK database, null if there are no matches
        """
        return self.fs.query([
            Filter('external_references.external_id', '=', id_code)
        ])

    def validate_mitre_att_by_id(self, id_code):
        """
        Checks the preface of the id_code and sends the id_code to specific functions
            This is done because using specified filters based on the type is about 20 times more efficient then
            the entire MITRE ATT&CK database with the id_code.
            There is a catch all provided in the case that there are new ID Code types added in the future
        :param id_code: The value of the mitre_att metadata tag
        :return: The return from the specified get_ function
        """
        if id_code.startswith('TA'):
            return self.get_tactic_by_id(id_code)
        elif id_code.startswith('T'):
            return self.get_technique_by_id(id_code)
        elif id_code.startswith('S'):
            return self.get_software_by_id(id_code)
        elif id_code.startswith('G'):
            return self.get_group_by_id(id_code)
        elif id_code.startswith('M'):
            return self.get_mitigation_by_id(id_code)
        else:
            return self.get_mitreattck_by_id(id_code)

    def valid_mitre_att(self, rule_to_validate_mitre_att, tag_index, tag_key):
        """
        Pulls the value of the mitre_att metadata tag and passes it to validate_mitre_att_by_id
        :param rule_to_validate_mitre_att: the plyara parsed rule that is being validated
        :param tag_index: used to reference what the array index of the mitre_att metadata tag is
        :param tag_key: the name of the metadata tag that is being processed
        :return: True if the value was found in the MITRE ATT&CK database and False if it was not found
        """
        MITRE_ATT = tag_key
        self.required_fields[MITRE_ATT].attributefound()
        self.required_fields_index[self.required_fields[MITRE_ATT].position].increment_count()

        metadata = rule_to_validate_mitre_att[METADATA]
        mitre_att_to_validate = str(metadata[tag_index][MITRE_ATT]).upper()
        metadata[tag_index][MITRE_ATT] = mitre_att_to_validate
        if self.validate_mitre_att_by_id(mitre_att_to_validate):
            self.required_fields[MITRE_ATT].attributevalid()
        else:
            self.required_fields[MITRE_ATT].attributeinvalid()

        return self.required_fields[MITRE_ATT].valid

    def valid_category(self, rule_to_validate_category, tag_index, tag_key):
        """
        Pulls the value of the category metadata tag and checks if it is a valid category type.
            Valid options are stored in self.category_types. If the category value is valid and a new metadata
            tag with a name the same as the category value is added to be searched for.
            This new metadata tag links to the same object as the initially created self.required_fields[CATEGORY_TYPE].
        :param rule_to_validate_category: the plyara parsed rule that is being validated
        :param tag_index: used to reference what the array index of the category metadata tag is
        :param tag_key: the name of the metadata tag that is being processed
        :return: True if the value was found in self.category_types and False if it was not found
        """
        CATEGORY = tag_key
        self.required_fields[CATEGORY].attributefound()
        self.required_fields_index[self.required_fields[CATEGORY].position].increment_count()
        child_tag_place_holder = self.required_fields[CATEGORY].argument.get("child_place_holder")

        metadata = rule_to_validate_category[METADATA]
        rule_category_to_check = metadata[tag_index][CATEGORY]
        if rule_category_to_check in self.category_types:
            self.required_fields[CATEGORY].attributevalid()
            add_category_type_to_required = {str(rule_category_to_check).lower(): self.required_fields[child_tag_place_holder]}
            self.required_fields_children.update(add_category_type_to_required)
        elif str(rule_category_to_check).upper() in self.category_types:
            rule_category_to_check = str(rule_category_to_check).upper()
            metadata[tag_index][CATEGORY] = rule_category_to_check
            self.required_fields[CATEGORY].attributevalid()
            add_category_type_to_required = {str(rule_category_to_check).lower(): self.required_fields[child_tag_place_holder]}
            self.required_fields_children.update(add_category_type_to_required)
        else:
            self.required_fields[CATEGORY].attributeinvalid()

        return self.required_fields[CATEGORY].valid

    def valid_category_type(self, rule_to_validate_type, tag_index, tag_key):
        """
        This will be called by the new tag created by the valid_category function. Because it references the same object
            as that initialized as CATEGORY_TYPE we can use that to reference the reqired tag in this function.
        :param rule_to_validate_type: the plyara parsed rule that is being validated
        :param tag_index: used to reference what the array index of the category_type metadata tag is
        :param tag_key: the name of the metadata tag that is being processed
        :return: True if the value matches the Regex expression and False if it was not found
        """
        CATEGORY = "category"
        child_tag_place_holder = self.required_fields[CATEGORY].argument.get("child_place_holder")
        self.required_fields[child_tag_place_holder].attributefound()
        self.required_fields_index[self.required_fields[child_tag_place_holder].position].increment_count()

        metadata = rule_to_validate_type[METADATA]
        rule_category_key_to_check = list(metadata[tag_index].keys())[0]
        rule_category_value_to_check = list(metadata[tag_index].values())[0]
        if re.fullmatch(CATEGORY_TYPE_REGEX, rule_category_value_to_check):
            self.required_fields[child_tag_place_holder].attributevalid()
        elif re.fullmatch(CATEGORY_TYPE_REGEX, str(rule_category_value_to_check).upper()):
            rule_category_value_to_check = str(rule_category_value_to_check).upper()
            metadata[tag_index][rule_category_key_to_check] = rule_category_value_to_check
            self.required_fields[child_tag_place_holder].attributevalid()
        else:
            self.required_fields[child_tag_place_holder].attributeinvalid()

        return self.required_fields[child_tag_place_holder].valid

    def validate_date(self, date_to_validate):
        """
        Verifies a date is in the correct format.
        :param date_to_validate: the value of the last_modifed metadata tag
        :return: True if the value is in the correct format or False if it is not valid
        """
        try:
            if date_to_validate != datetime.datetime.strptime(date_to_validate, "%Y-%m-%d").strftime('%Y-%m-%d'):
                raise ValueError
            return True
        except ValueError:
            return False

    def current_valid_date(self):
        """
        Generates the current date in the valid format
        :return: the current date in the valid format
        """
        return datetime.datetime.now().strftime('%Y-%m-%d')

    def valid_last_modified(self, rule_to_date_check, tag_index, tag_key):
        """
        This value can be generated: there is the option to verify if an existing date is correct, insert a generated
            date if none was found and if the potential default metadata index would be out of bounds appends a
                generated date
        :param rule_to_date_check: the plyara parsed rule that is being validated
        :param tag_index: used to reference what the array index of the last_modified metadata tag is
        :param tag_key: the name of the metadata tag that is being processed
        :return: True if the value matches the valid date format and False if it does not match it
        """
        LAST_MODIFIED = tag_key
        self.required_fields[LAST_MODIFIED].attributefound()
        self.required_fields_index[self.required_fields[LAST_MODIFIED].position].increment_count()

        current_date = self.current_valid_date()
        if self.valid_metadata_index(rule_to_date_check, tag_index):
            if list(rule_to_date_check[METADATA][tag_index].keys())[0] == LAST_MODIFIED:
                rule_to_date_check[METADATA][tag_index][LAST_MODIFIED] = current_date
                if self.validate_date(list(rule_to_date_check[METADATA][tag_index].values())[0]):
                    self.required_fields[LAST_MODIFIED].attributevalid()
                else:
                    self.required_fields[LAST_MODIFIED].attributeinvalid()
            else:
                rule_date = {LAST_MODIFIED: current_date}
                rule_to_date_check[METADATA].insert(tag_index, rule_date)
                self.required_fields[LAST_MODIFIED].attributevalid()
        else:
            rule_date = {LAST_MODIFIED: current_date}
            rule_to_date_check[METADATA].append(rule_date)
            self.required_fields[LAST_MODIFIED].attributevalid()

        return self.required_fields[LAST_MODIFIED].valid

    def valid_first_imported(self, rule_to_date_check, tag_index, tag_key):
        """
        This value can be generated: there is the option to verify if an existing date is correct, insert a generated
            date if none was found and if the potential default metadata index would be out of bounds appends
            a generated date
        :param rule_to_date_check: the plyara parsed rule that is being validated
        :param tag_index: used to reference what the array index of the last_modified metadata tag is
        :param tag_key: the name of the metadata tag that is being processed
        :return: True if the value matches the valid date format and False if it does not match it
        """
        FIRST_IMPORTED = tag_key
        self.required_fields[FIRST_IMPORTED].attributefound()
        self.required_fields_index[self.required_fields[FIRST_IMPORTED].position].increment_count()

        if self.valid_metadata_index(rule_to_date_check, tag_index):
            if list(rule_to_date_check[METADATA][tag_index].keys())[0] == FIRST_IMPORTED:
                if self.validate_date(list(rule_to_date_check[METADATA][tag_index].values())[0]):
                    self.required_fields[FIRST_IMPORTED].attributevalid()
                else:
                    self.required_fields[FIRST_IMPORTED].attributeinvalid()
            else:
                rule_date = {FIRST_IMPORTED: self.current_valid_date()}
                rule_to_date_check[METADATA].insert(tag_index, rule_date)
                self.required_fields[FIRST_IMPORTED].attributevalid()
        else:
            rule_date = {FIRST_IMPORTED: self.current_valid_date()}
            rule_to_date_check[METADATA].append(rule_date)
            self.required_fields[FIRST_IMPORTED].attributevalid()

        return self.required_fields[FIRST_IMPORTED].valid

    def valid_source(self, rule_to_source_check, tag_index, tag_key):
        """
        Validates the source
        :param rule_to_source_check:
        :param tag_index: used to reference what the array index of the last_modified metadata tag is
        :param tag_key: the name of the metadata tag that is being processed
        :return: True if the value matches the UNIVERSAL_REGEX and False if it does not match it
        """
        SOURCE = tag_key
        REFERENCE = self.required_fields[SOURCE].argument.get("required")
        self.required_fields[SOURCE].attributefound()
        self.required_fields_index[self.required_fields[SOURCE].position].increment_count()

        metadata = rule_to_source_check[METADATA]
        source_to_check = metadata[tag_index][SOURCE]
        if re.fullmatch(UNIVERSAL_REGEX, source_to_check):
            self.required_fields[SOURCE].attributevalid()
        elif re.fullmatch(UNIVERSAL_REGEX, str(source_to_check).upper()):
            source_to_check = str(source_to_check).upper()
            metadata[tag_index][SOURCE] = source_to_check
            self.required_fields[SOURCE].attributevalid()
        else:
            self.required_fields[SOURCE].attributeinvalid()

        if re.fullmatch(OPENSOURCE_REGEX, source_to_check):
            # Because the source is OPENSOURCE a reference is required
            self.required_fields[REFERENCE].optional = TagOpt.REQ_PROVIDED

        return self.required_fields[SOURCE].valid

    def valid_version(self, rule_to_version_check, tag_index, tag_key):
        """
        This value can be generated: there is the option to verify if an existing version format is correct, insert a
            generated version if none was found and if the potential default metadata index would be out of bounds
            appends a generated version
        :param rule_to_version_check: the plyara parsed rule that is being validated
        :param tag_index: used to reference what the array index of the version metadata tag is
        :param tag_key: the name of the metadata tag that is being processed
        :return: True if the version is of the correct format and False if it is not
        """
        VERSION = tag_key
        self.required_fields[VERSION].attributefound()
        self.required_fields_index[self.required_fields[VERSION].position].increment_count()

        rule_version = {VERSION: '1.0'}
        if self.valid_metadata_index(rule_to_version_check, tag_index):
            if list(rule_to_version_check[METADATA][tag_index].keys())[0] == VERSION:
                if isinstance(version.parse(list(rule_to_version_check[METADATA][tag_index].values())[0]), version.Version):
                    self.required_fields[VERSION].attributevalid()
                else:
                    self.required_fields[VERSION].attributeinvalid()
            else:
                rule_to_version_check[METADATA].insert(tag_index, rule_version)
                self.required_fields[VERSION].attributevalid()
        else:
            rule_to_version_check[METADATA].append(rule_version)
            self.required_fields[VERSION].attributevalid()

        return self.required_fields[VERSION].valid

    def valid_al_config_dumper(self, rule_to_validate_al_config_d, tag_index, tag_key):
        """
        Makes the al_config_parser metadata tag required if this is found first.
        :param rule_to_validate_al_config_d: the plyara parsed rule that is being validated
        :param tag_index: used to reference what the array index of the actor metadata tag is
        :param tag_key: the name of the metadata tag that is being processed
        :return: True all the time because the value is never verified...
        """
        AL_CONFIG_D = tag_key
        self.required_fields[AL_CONFIG_D].attributefound()
        self.required_fields_index[self.required_fields[AL_CONFIG_D].position].increment_count()

        # Because there is an al_config_dumper al_config_parser becomes required
        self.required_fields[AL_CONFIG_D].optional = TagOpt.REQ_PROVIDED

        # Because we are not validating the value... So much pain!
        self.required_fields[AL_CONFIG_D].attributevalid()

        return self.required_fields[AL_CONFIG_D].valid

    def valid_al_config_parser(self, rule_to_validate_al_config_p, tag_index, tag_key):
        """
        Makes the al_config_dumper metadata tag required if this is found first.
        :param rule_to_validate_al_config_p: the plyara parsed rule that is being validated
        :param tag_index: used to reference what the array index of the actor metadata tag is
        :param tag_key: the name of the metadata tag that is being processed
        :return: True all the time because the value is never verified...
        """
        AL_CONFIG_P = tag_key
        self.required_fields[AL_CONFIG_P].attributefound()
        self.required_fields_index[self.required_fields[AL_CONFIG_P].position].increment_count()

        # Because there is an al_config_parser al_config_dumper becomes required
        self.required_fields[AL_CONFIG_P].optional = TagOpt.REQ_PROVIDED

        # Because we are not validating the value... So much pain!
        self.required_fields[AL_CONFIG_P].attributevalid()

        return self.required_fields[AL_CONFIG_P].valid

    def warning_check(self, rule_to_check, valid):
        """
        Loops through all of the potential warning functions.
        :param rule_to_check: the finalized rule
        :param valid: the rule's YaraValidatorReturn
        :return:
        """
        for warning in self.warning_functions:
            warning(rule_to_check, valid)

    def warning_author_no_report_check(self, rule_to_check, valid):
        if self.required_fields.get(AUTHOR) and self.required_fields.get(REPORT):
            if self.required_fields[AUTHOR].found and not self.required_fields[REPORT].found:
                metadata_tags = rule_to_check[METADATA]
                for tag in metadata_tags:
                    if len(tag.keys()) == 1:
                        key = list(tag.keys())[0]
                        value = list(tag.values())[0]
                        if key == AUTHOR and (value == "RevEng@CCCS" or value == "reveng@CCCS"):
                            valid.update_warning(True, REPORT, "Rule is authored by the CCCS but no report is referenced.")

    def warning_author_no_hash_check(self, rule_to_check, valid):
        if self.required_fields.get(AUTHOR) and self.required_fields.get(HASH):
            if self.required_fields[AUTHOR].found and not self.required_fields[HASH].found:
                metadata_tags = rule_to_check[METADATA]
                for tag in metadata_tags:
                    if len(tag.keys()) == 1:
                        key = list(tag.keys())[0]
                        value = list(tag.values())[0]
                        if key == AUTHOR and value == "RevEng@CCCS":
                            valid.update_warning(True, HASH, "Rule is authored by the CCCS but no hash is referenced.")

    def warning_actor_no_mitre_group(self, rule_to_check, valid):
        if self.required_fields.get(ACTOR) and self.required_fields[ACTOR].argument.get("child_place_holder"):
            place_holder = self.required_fields[ACTOR].argument.get("child_place_holder")
            if self.required_fields[ACTOR].found and not self.required_fields[place_holder].found:
                metadata_tags = rule_to_check[METADATA]
                for tag in metadata_tags:
                    if len(tag.keys()) == 1:
                        key = list(tag.keys())[0]
                        value = list(tag.values())[0]
                        if key == ACTOR:
                            warning_message = "Actor: " + value + " was not found in MITRE ATT&CK dataset."
                            valid.update_warning(True, ACTOR, warning_message)

    def generate_required_optional_tags(self, rule_to_validate):
        req_optional_keys = self.return_req_optional()

        for key in req_optional_keys:
            if not self.required_fields[key].found:
                if self.required_fields[key].function == self.valid_regex:
                    self.required_fields[key].attributefound()
                else:
                    self.required_fields[key].function(rule_to_validate, self.required_fields_index[self.required_fields[key].position].index(), key)

    def return_req_optional(self):
        keys_to_return = []
        for key in self.required_fields.keys():
            if self.required_fields[key].optional == TagOpt.REQ_OPTIONAL:
                if not self.required_fields[key].found:
                    keys_to_return.append(key)

        if self.mitre_group_alias and self.required_fields[ACTOR].found:
            keys_to_return.append(self.required_fields[ACTOR].argument.get("child_place_holder"))
        return keys_to_return

    def __parse_scheme(self, cfg_to_parse):
        cfg_being_parsed = ""
        for index, cfg in enumerate(self.scheme[cfg_to_parse]):
            if index > 0:
                cfg_being_parsed = cfg_being_parsed + "|"

            cfg_being_parsed = cfg_being_parsed + "^" + str(cfg['value']) + "$"

        return cfg_being_parsed

    def handle_child_parent_tags(self, tag, params, tags_in_child_parent_relationship, place_holder="_child"):
        """
        Child tags create TagAttributes instances as temporary place holders in self.required_fields and
        the place holders will be used to create the actual TagAttributes instances in self.required_fields_children.
        This method creates a place holder for a child tag and adds the name of the place holder to a parent tag.
        :param tag: string name of a tag in CCCS_Yara.yml file
        :param params: parameters of the corresponding tag in a dictionary format
        :param place_holder: string to be attached to a tag name -> will be used as a place holder name
        :param tags_in_child_parent_relationship: list of tags that contain either parent or child argument
        :return: void
        """
        argument = params.get("argument")
        if argument:
            if argument.get("parent"):
                self.required_fields[tag + place_holder] = self.required_fields.pop(tag)
                tags_in_child_parent_relationship.append(argument.get("parent"))
            elif argument.get("child"):
                child_tag = argument["child"]
                argument.update({"child_place_holder": child_tag + place_holder})
                tags_in_child_parent_relationship.append(argument.get("child"))

    def validate_child_parent_tags(self, configuration, tags_in_child_parent_relationship):
        """
        Checks if any tags in child-parent relationships are missing from CCCS_Yara.yml configuration page
        :param configuration: CCCS_Yara.yml configuration in dictionary format
        :param tags_in_child_parent_relationship: a list of tags in child-parent relationships
        :return: void
        """
        for tag in tags_in_child_parent_relationship:
            if configuration.get(tag) is None:
                print("CCCS_Yara.yml: \"" + tag + "\" is required (in a child-parent relationship)")
                exit(1)

    def read_regex_values(self, file_name, regex_tag):
        """
        Parses multiple values under the name "regex_tag" from given YAML file to make a single line of expression
        :param file_name: name of the file to reference
        :param regex_tag: name of the tag in the file that contains multiple regex expressions
        :return: single line of regex expression
        """
        regex_yaml_path = SCRIPT_LOCATION.parent / file_name
        with open(regex_yaml_path, "r") as yaml_file:
            scheme = yaml.safe_load(yaml_file)

        cfg_being_parsed = ""
        for index, cfg in enumerate(scheme[regex_tag]):
            if index > 0:
                cfg_being_parsed = cfg_being_parsed + "|"

            cfg_being_parsed = cfg_being_parsed + "^" + str(cfg['value']) + "$"

        return cfg_being_parsed

    def read_yara_cfg(self, tag, params, tag_position):
        """
        Creates a TagAttributes object for self.required_fields based on the CCCS_Yara.yml configuration
        :param tag: string name of a tag in CCCS_Yara.yml file
        :param params: parameters of the corresponding metadata tag in dictionary format
        :param tag_position: index (position) of the key in CCCS_Yara.yml file
        :return: TagAttributes instance
        """
        # parameters for creating a TagAttributes instance
        tag_max_count = None
        tag_optional = None
        tag_validator = None
        tag_argument = None

        # check if the tag is optional
        optional = params.get("optional")
        if optional is not None:
            if optional is True or re.fullmatch("(?i)^y$|yes", str(optional)):
                tag_optional = TagOpt.OPT_OPTIONAL
            elif optional is False or re.fullmatch("(?i)^n$|no", str(optional)):
                tag_optional = TagOpt.REQ_PROVIDED
            elif re.fullmatch("(?i)optional", str(optional)):
                tag_optional = TagOpt.REQ_OPTIONAL
            else:
                print("CCCS_Yara.yml: \"" + tag + "\" has an invalid parameter - optional")
                exit(1)
        else:
            print("CCCS_Yara.yml: \"" + tag + "\" has a missing parameter - optional")
            exit(1)

        # check if the tag is unique
        unique = params.get("unique")
        if unique is not None:
            if unique is True or re.fullmatch("(?i)^y$|yes", str(unique)):
                tag_max_count = 1
            elif unique is False or re.fullmatch("(?i)^n$|no", str(unique)):
                tag_max_count = -1
            elif isinstance(unique, int):
                tag_max_count = unique
            else:
                print("CCCS_Yara.yml: \"" + tag + "\" has an invalid parameter - unique")
                exit(1)
        else:
            print("CCCS_Yara.yml: \"" + tag + "\" has a missing parameter - unique")
            exit(1)

        # check which validator to use
        if params.get("validator"):  # validate the corresponding tag using the "validator"
            tag_validator = self.validators.get(params["validator"])
            if not tag_validator:
                print("CCCS_Yara.yml: Validatior \"" + params["validator"] + "\" of \"" + tag + "\" is not defined")
                exit(1)

            tag_argument = params.get("argument")

            if tag_validator == self.valid_regex:  # argument must have "regex expression" parameter when using "valid_regex"
                if tag_argument is None:  # if argument field is empty or does not exist
                    print("CCCS_Yara.yml: \"" + tag + "\" has a missing parameter - argument")
                    exit(1)

                elif isinstance(tag_argument, dict):
                    input_fileName = tag_argument.get("fileName")
                    input_valueName = tag_argument.get("valueName")
                    input_regexExpression= tag_argument.get("regexExpression")

                    # check if fileName/valueName and regexExpression are mutually exclusive
                    if input_fileName:
                        if input_valueName:
                            if input_regexExpression:
                                print("CCCS_Yara.yml: \"" + tag + "\" has too many parameters - fileName | valueName | regexExpression")
                                exit(1)
                            else:
                                tag_argument.update({"regexExpression": self.read_regex_values(input_fileName, input_valueName)})
                        else:
                            if input_regexExpression:
                                print("CCCS_Yara.yml: \"" + tag + "\" has too many parameters - fileName | regexExpression")
                                exit(1)
                            else:
                                print("CCCS_Yara.yml: \"" + tag + "\" is missing a parameter - valueName")
                                exit(1)
                    else:
                        if input_valueName:
                            if input_regexExpression:
                                print("CCCS_Yara.yml: \"" + tag + "\" has too many parameters - valueName | regexExpression")
                                exit(1)
                            else:
                                print("CCCS_Yara.yml: \"" + tag + "\" is missing a parameter - fileName")
                                exit(1)
                        elif not input_regexExpression:
                            print("CCCS_Yara.yml: \"" + tag + "\" is missing a parameter - regexExpression")
                            exit(1)
                else:
                    print("CCCS_Yara.yml: \"" + tag + "\" has a parameter with invalid format - argument")
                    exit(1)
        else:
            print("CCCS_Yara.yml: \"" + tag + "\" has a missing parameter - validator")
            exit(1)

        return TagAttributes(tag_validator, tag_optional, tag_max_count, tag_position, tag_argument)

    def import_yara_cfg(self):
        """
        Updates self.required_fields based on the CCCS_Yara.yml configuration
        :return: void
        """
        tags_in_child_parent_relationship = []
        for index, item in enumerate(self.yara_config.items()):  # python 3.6+ dictionary preserves the insertion order
            cfg_tag = item[0]
            cfg_params = item[1]  # {parameter : value}

            self.required_fields[cfg_tag] = self.read_yara_cfg(cfg_tag, cfg_params, index)  # add a new TagAttributes instance
            self.handle_child_parent_tags(cfg_tag, cfg_params, tags_in_child_parent_relationship)  # replace the name of child tag with its place holder
        self.validate_child_parent_tags(self.yara_config, tags_in_child_parent_relationship)  # check if any tags in child-parent relationship are missing

    def __init__(self):
        # initialize the file system source for the MITRE ATT&CK data
        self.fs = FileSystemSource(MITRE_STIX_DATA_PATH)

        with open(VALIDATOR_YAML_PATH, "r") as yaml_file:
            self.scheme = yaml.safe_load(yaml_file)

        with open(CONFIGURATION_YAML_PATH, "r") as config_file:
            self.yara_config = yaml.safe_load(config_file)

        self.validators = {
            "valid_regex": self.valid_regex,
            "valid_uuid": self.valid_uuid,
            "valid_fingerprint": self.valid_fingerprint,
            "valid_version": self.valid_version,
            "valid_first_imported": self.valid_first_imported,
            "valid_last_modified": self.valid_last_modified,
            "valid_source": self.valid_source,
            "valid_category": self.valid_category,
            "valid_category_type": self.valid_category_type,
            "valid_mitre_att": self.valid_mitre_att,
            "valid_actor": self.valid_actor,
            "mitre_group_generator": self.mitre_group_generator,
            "valid_al_config_dumper": self.valid_al_config_dumper,
            "valid_al_config_parser": self.valid_al_config_parser
        }

        self.required_fields = {}
        self.import_yara_cfg()

        self.required_fields_index = [Positional(i) for i in range(len(self.required_fields))]

        self.category_types = self.__parse_scheme('category_types')
        self.mitre_group_alias = None
        self.mitre_group_alias_regex = "^[A-Z 0-9\.-]+$"
        self.required_fields_children = {}

        self.warning_functions = [
            self.warning_author_no_report_check,
            self.warning_author_no_hash_check,
            self.warning_actor_no_mitre_group
        ]
