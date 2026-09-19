from rest_framework.exceptions import ParseError
from rest_framework.parsers import JSONParser


class NestingSafeJSONParser(JSONParser):
    """
    JSON nested tens of thousands of levels deep exhausts Python's recursion
    limit inside the parser. DRF only expects malformed JSON there (a
    ValueError), so the RecursionError would surface as a 500 - and on the
    public endpoints anyone can send such a body.

    It is caught here, where it can only mean "this input is absurd", and not
    in the exception handler: there, a RecursionError could just as well be a
    real bug in our own code, which must stay a 500.
    """

    def parse(self, stream, media_type=None, parser_context=None):
        try:
            return super().parse(stream, media_type, parser_context)
        except RecursionError as error:
            raise ParseError("The JSON is nested too deeply.") from error
