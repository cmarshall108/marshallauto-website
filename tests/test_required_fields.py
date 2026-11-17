import unittest

from bs4 import BeautifulSoup
from flask_wtf import FlaskForm
from wtforms.validators import DataRequired, InputRequired

from app import create_app, forms
from config import TestingConfig


def _required(validators):
    return any(isinstance(validator, (DataRequired, InputRequired)) for validator in validators)


class RequiredFieldMarkingTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestingConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()

    def test_required_wtforms_labels_end_with_asterisk_and_render_required(self):
        unmarked = []
        missing_attr = []
        for form_cls in vars(forms).values():
            if not isinstance(form_cls, type) or not issubclass(form_cls, FlaskForm) or form_cls is FlaskForm:
                continue
            form = form_cls()
            for field in form:
                if field.type in {'SubmitField', 'HiddenField', 'CSRFTokenField'}:
                    continue
                rendered = BeautifulSoup(str(field()), 'html.parser').find(True)
                marked_required = rendered is not None and rendered.has_attr('required')
                if not _required(field.validators):
                    if marked_required:
                        missing_attr.append(f'{form_cls.__name__}.{field.name} unexpectedly required')
                    continue
                if not str(field.label.text).rstrip().endswith('*'):
                    unmarked.append(f'{form_cls.__name__}.{field.name}')
                if not marked_required:
                    missing_attr.append(f'{form_cls.__name__}.{field.name}')
        self.assertEqual(unmarked, [])
        self.assertEqual(missing_attr, [])
