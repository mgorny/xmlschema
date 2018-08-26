# -*- coding: utf-8 -*-
#
# Copyright (c), 2016-2018, SISSA (International School for Advanced Studies).
# All rights reserved.
# This file is distributed under the terms of the MIT License.
# See the file 'LICENSE' in the root directory of the present
# distribution, or http://opensource.org/licenses/MIT.
#
# @author Davide Brunato <brunato@sissa.it>
#
"""
This module contains classes for XML Schema elements, complex types and model groups.
"""
from decimal import Decimal

from ..compat import unicode_type
from ..exceptions import XMLSchemaAttributeError
from ..etree import etree_element, is_etree_element
from ..converters import ElementData, XMLSchemaConverter
from ..qnames import (
    XSD_GROUP_TAG, XSD_SEQUENCE_TAG, XSD_ALL_TAG, XSD_CHOICE_TAG, XSD_ATTRIBUTE_GROUP_TAG,
    XSD_COMPLEX_TYPE_TAG, XSD_SIMPLE_TYPE_TAG, XSD_ALTERNATIVE_TAG, XSD_ELEMENT_TAG, XSD_ANY_TYPE,
    XSD_UNIQUE_TAG, XSD_KEY_TAG, XSD_KEYREF_TAG, XSI_NIL, XSI_TYPE, reference_to_qname, get_qname
)
from ..xpath import ElementPathMixin

from .exceptions import XMLSchemaValidationError
from .parseutils import get_xsd_attribute, get_xsd_bool_attribute, get_xsd_derivation_attribute
from .xsdbase import XsdComponent, XsdType, ParticleMixin, ValidationMixin
from .constraints import XsdUnique, XsdKey, XsdKeyref
from .wildcards import XsdAnyElement


XSD_MODEL_GROUP_TAGS = {XSD_GROUP_TAG, XSD_SEQUENCE_TAG, XSD_ALL_TAG, XSD_CHOICE_TAG}
XSD_ATTRIBUTE_GROUP_ELEMENT = etree_element(XSD_ATTRIBUTE_GROUP_TAG)


class XsdElement(XsdComponent, ValidationMixin, ParticleMixin, ElementPathMixin):
    """
    Class for XSD 1.0 'element' declarations.
    
    <element
      abstract = boolean : false
      block = (#all | List of (extension | restriction | substitution))
      default = string
      final = (#all | List of (extension | restriction))
      fixed = string
      form = (qualified | unqualified)
      id = ID
      maxOccurs = (nonNegativeInteger | unbounded)  : 1
      minOccurs = nonNegativeInteger : 1
      name = NCName
      nillable = boolean : false
      ref = QName
      substitutionGroup = QName
      type = QName
      {any attributes with non-schema namespace . . .}>
      Content: (annotation?, ((simpleType | complexType)?, (unique | key | keyref)*))
    </element>
    """
    admitted_tags = {XSD_ELEMENT_TAG}

    def __init__(self, elem, schema, parent, name=None):
        super(XsdElement, self).__init__(elem, schema, parent, name)
        if not hasattr(self, 'type'):
            raise XMLSchemaAttributeError("undefined 'type' attribute for %r." % self)
        if not hasattr(self, 'qualified'):
            raise XMLSchemaAttributeError("undefined 'qualified' attribute for %r." % self)

    def __repr__(self):
        if self.ref is None:
            return u'%s(name=%r, occurs=%r)' % (self.__class__.__name__, self.prefixed_name, self.occurs)
        else:
            return u'%s(ref=%r, occurs=%r)' % (self.__class__.__name__, self.prefixed_name, self.occurs)

    def __setattr__(self, name, value):
        if name == "type":
            assert value is None or isinstance(value, XsdType), "Wrong value %r for attribute 'type'." % value
            if hasattr(value, 'attributes'):
                self.attributes = value.attributes
            else:
                self.attributes = self.schema.BUILDERS.attribute_group_class(
                    XSD_ATTRIBUTE_GROUP_ELEMENT, self.schema, self
                )
        super(XsdElement, self).__setattr__(name, value)

    def __iter__(self):
        try:
            content_iterator = self.type.content_type.iter_elements()
        except AttributeError:
            return  # It's a simple type or simple content element
        else:
            for e in content_iterator:
                yield e

    def _parse(self):
        XsdComponent._parse(self)
        self._parse_attributes()
        index = self._parse_type()
        if self.type is None:
            self.type = self.maps.lookup_type(XSD_ANY_TYPE)

        self._parse_constraints(index)
        self._parse_substitution_group()

    def _parse_attributes(self):
        self._parse_particle(self.elem)
        self.name = None
        self._ref = None
        self.qualified = self.elem.get('form', self.schema.element_form_default) == 'qualified'

        if self.default is not None and self.fixed is not None:
            self.parse_error("'default' and 'fixed' attributes are mutually exclusive.")
        self._parse_properties('abstract', 'block', 'final', 'form', 'nillable')

        # Parse element attributes
        try:
            element_name = reference_to_qname(self.elem.attrib['ref'], self.namespaces)
        except KeyError:
            # No 'ref' attribute ==> 'name' attribute required.
            try:
                if self.is_global or self.qualified:
                    self.name = get_qname(self.target_namespace, self.elem.attrib['name'])
                else:
                    self.name = self.elem.attrib['name']
            except KeyError:
                self.parse_error("missing both 'name' and 'ref' attributes.")

            if self.is_global:
                if 'minOccurs' in self.elem.attrib:
                    self.parse_error("attribute 'minOccurs' not allowed for a global element.")
                if 'maxOccurs' in self.elem.attrib:
                    self.parse_error("attribute 'maxOccurs' not allowed for a global element.")
        else:
            # Reference to a global element
            if self.is_global:
                self.parse_error("an element reference can't be global.")
            for attribute in ('name', 'type', 'nillable', 'default', 'fixed', 'form', 'block'):
                if attribute in self.elem.attrib:
                    self.parse_error("attribute %r is not allowed when element reference is used." % attribute)
            try:
                xsd_element = self.maps.lookup_element(element_name)
            except KeyError:
                self.parse_error('unknown element %r' % element_name)
                self.name = element_name
                self.type = self.maps.lookup_type(XSD_ANY_TYPE)
            else:
                self._ref = xsd_element
                self.name = xsd_element.name
                self.type = xsd_element.type
                self.qualified = xsd_element.qualified

    def _parse_type(self):
        if self.ref:
            if self._parse_component(self.elem, required=False, strict=False) is not None:
                self.parse_error("element reference declaration can't has children.")
        elif 'type' in self.elem.attrib:
            type_qname = reference_to_qname(self.elem.attrib['type'], self.namespaces)
            try:
                self.type = self.maps.lookup_type(type_qname)
            except KeyError:
                self.parse_error('unknown type %r' % self.elem.attrib['type'])
                self.type = self.maps.lookup_type(XSD_ANY_TYPE)
        else:
            child = self._parse_component(self.elem, required=False, strict=False)
            if child is not None:
                if child.tag == XSD_COMPLEX_TYPE_TAG:
                    self.type = self.schema.BUILDERS.complex_type_class(child, self.schema, self)
                elif child.tag == XSD_SIMPLE_TYPE_TAG:
                    self.type = self.schema.BUILDERS.simple_type_factory(child, self.schema, self)
                return 1
            else:
                self.type = None
        return 0

    def _parse_constraints(self, index=0):
        self.constraints = {}
        for child in self._iterparse_components(self.elem, start=index):
            if child.tag == XSD_UNIQUE_TAG:
                constraint = XsdUnique(child, self.schema, self)
            elif child.tag == XSD_KEY_TAG:
                constraint = XsdKey(child, self.schema, self)
            elif child.tag == XSD_KEYREF_TAG:
                constraint = XsdKeyref(child, self.schema, self)
            else:
                continue  # Error already caught by validation against the meta-schema

            try:
                if child != self.maps.constraints[constraint.name]:
                    self.parse_error(u"duplicated identity constraint %r:" % constraint.name, child)
            except KeyError:
                self.maps.constraints[constraint.name] = child
            finally:
                self.constraints[constraint.name] = constraint

    def _parse_substitution_group(self):
        substitution_group = self.substitution_group
        if substitution_group is None:
            return

        if not self.is_global:
            self.parse_error(u"'substitutionGroup' attribute in a local element declaration")

        qname = reference_to_qname(substitution_group, self.namespaces)
        if qname[0] != '{':
            qname = get_qname(self.target_namespace, qname)
        try:
            head_element = self.maps.lookup_element(qname)
        except KeyError:
            self.parse_error("unknown substitutionGroup %r" % substitution_group)
        else:
            final = head_element.final
            if final is None:
                final = self.schema.final_default

            if final == '#all' or 'extension' in final and 'restriction' in final:
                self.parse_error("head element %r cannot be substituted." % head_element)
            elif self.type == head_element.type or self.type.name == XSD_ANY_TYPE:
                pass
            elif 'extension' in final and not self.type.is_derived(head_element.type, 'extension'):
                self.parse_error(
                    "%r type is not of the same or an extension of the head element %r type."
                    % (self, head_element)
                )
            elif 'restriction' in final and not self.type.is_derived(head_element.type, 'restriction'):
                self.parse_error(
                    "%r type is not of the same or a restriction of the head element %r type."
                    % (self, head_element)
                )
            elif not self.type.is_derived(head_element.type):
                self.parse_error(
                    "%r type is not of the same or a derivation of the head element %r type."
                    % (self, head_element)
                )

    def validation_error(self, error, validation, obj=None, **kwargs):
        if not isinstance(error, XMLSchemaValidationError):
            error = XMLSchemaValidationError(self, obj, reason=unicode_type(error))

        if error.schema_elem is None:
            if self.type.name is not None and self.target_namespace == self.type.target_namespace:
                error.schema_elem = self.type.elem
            else:
                error.schema_elem = self.elem
        if error.elem is None and is_etree_element(obj):
            error.elem = obj
        return super(XsdElement, self).validation_error(error, validation, **kwargs)

    @property
    def built(self):
        return self.type.is_global or self.type.built

    @property
    def validation_attempted(self):
        if self.built:
            return 'full'
        else:
            return self.type.validation_attempted

    # XSD declaration attributes
    @property
    def ref(self):
        return self.elem.get('ref')

    # Global element's exclusive properties
    @property
    def final(self):
        return get_xsd_derivation_attribute(self.elem, 'final', ('extension', 'restriction'))

    @property
    def block(self):
        return get_xsd_derivation_attribute(self.elem, 'block', ('extension', 'restriction', 'substitution'))

    @property
    def substitution_group(self):
        return self.elem.get('substitutionGroup')

    # Properties inherited by references
    @property
    def abstract(self):
        if self._ref is not None:
            return self._ref.abstract
        return get_xsd_bool_attribute(self.elem, 'abstract', default=False)

    @property
    def default(self):
        return self.elem.get('default') if self._ref is None else self._ref.default

    @property
    def fixed(self):
        return self.elem.get('fixed') if self._ref is None else self._ref.fixed

    @property
    def form(self):
        if self._ref is not None:
            return self._ref.form
        return get_xsd_attribute(self.elem, 'form', ('qualified', 'unqualified'), default=None)

    @property
    def nillable(self):
        if self._ref is not None:
            return self._ref.nillable
        return get_xsd_bool_attribute(self.elem, 'nillable', default=False)

    def get_attribute(self, name):
        if name[0] != '{':
            return self.type.attributes[get_qname(self.type.target_namespace, name)]
        return self.type.attributes[name]

    def iter_components(self, xsd_classes=None):
        if xsd_classes is None:
            yield self
            for obj in self.constraints.values():
                yield obj
        else:
            if isinstance(self, xsd_classes):
                yield self
            for obj in self.constraints.values():
                if isinstance(obj, xsd_classes):
                    yield obj

        if self.ref is None and not self.type.is_global:
            for obj in self.type.iter_components(xsd_classes):
                yield obj

    def iter_decode(self, elem, validation='lax', converter=None, **kwargs):
        """
        Creates an iterator for decoding an Element instance.

        :param elem: the Element that has to be decoded.
        :param validation: the validation mode, can be 'lax', 'strict' or 'skip.
        :param converter: an :class:`XMLSchemaConverter` subclass or instance.
        :param kwargs: keyword arguments for the decoding process.
        :return: yields a decoded object, eventually preceded by a sequence of \
        validation or decoding errors.
        """
        if not isinstance(converter, XMLSchemaConverter):
            converter = self.schema.get_converter(converter, **kwargs)
        level = kwargs.pop('level', 0)
        use_defaults = kwargs.get('use_defaults', False)
        value = children = attributes = None

        # Get the instance type: xsi:type or the schema's declaration
        if XSI_TYPE in elem.attrib:
            xsd_type = self.maps.lookup_type(converter.unmap_qname(elem.attrib[XSI_TYPE]))
            attribute_group = getattr(xsd_type, 'attributes', self.attributes)
        else:
            xsd_type = self.type
            attribute_group = self.attributes

        # Decode attributes
        for result in attribute_group.iter_decode(elem.attrib, validation, **kwargs):
            if isinstance(result, XMLSchemaValidationError):
                yield self.update_error(result, elem, **kwargs)
            else:
                attributes = result

        # Checks the xsi:nil attribute of the instance
        if validation != 'skip' and XSI_NIL in elem.attrib:
            if not self.nillable:
                yield self.validation_error(validation, "element is not nillable.", elem, **kwargs)
            try:
                if get_xsd_bool_attribute(elem, XSI_NIL):
                    if elem.text is not None:
                        reason = "xsi:nil='true' but the element is not empty."
                        yield self.validation_error(validation, reason, elem, **kwargs)
                    else:
                        element_data = ElementData(elem.tag, None, None, attributes)
                        yield converter.element_decode(element_data, self, level)
                        return
            except TypeError:
                reason = "xsi:nil attribute must has a boolean value."
                yield self.validation_error(validation, reason, elem, **kwargs)

        if xsd_type.is_simple():
            if len(elem) and validation != 'skip':
                reason = "a simpleType element can't has child elements."
                yield self.validation_error(validation, reason, elem, **kwargs)

            text = elem.text
            if self.fixed is not None:
                if text is None:
                    text = self.fixed
                elif text != self.fixed:
                    reason = "must has the fixed value %r." % self.fixed
                    yield self.validation_error(validation, reason, elem, **kwargs)
            elif not text and use_defaults and self.default is not None:
                text = self.default

            if text is None:
                for result in xsd_type.iter_decode('', validation, **kwargs):
                    if isinstance(result, XMLSchemaValidationError):
                        yield self.update_error(result, elem, **kwargs)
            else:
                for result in xsd_type.iter_decode(text, validation, **kwargs):
                    if isinstance(result, XMLSchemaValidationError):
                        yield self.update_error(result, elem, **kwargs)
                    else:
                        value = result

        elif xsd_type.has_simple_content():
            if len(elem) and validation != 'skip':
                reason = "a simple content element can't has child elements."
                yield self.validation_error(validation, reason, elem, **kwargs)

            if elem.text is not None:
                text = elem.text or self.default if use_defaults else elem.text
                for result in xsd_type.content_type.iter_decode(text, validation, **kwargs):
                    if isinstance(result, XMLSchemaValidationError):
                        yield self.update_error(result, elem, **kwargs)
                    else:
                        value = result
        else:
            for result in xsd_type.content_type.iter_decode(elem, validation, converter, level=level + 1, **kwargs):
                if isinstance(result, XMLSchemaValidationError):
                    yield self.update_error(result, elem, **kwargs)
                else:
                    children = result

        if isinstance(value, Decimal):
            try:
                value = kwargs['decimal_type'](value)
            except (KeyError, TypeError):
                pass

        element_data = ElementData(elem.tag, value, children, attributes)
        yield converter.element_decode(element_data, self, level)
        if children is not None:
            del children

        if validation != 'skip':
            for constraint in self.constraints.values():
                for error in constraint(elem):
                    yield self.update_error(error, elem, **kwargs)

    def iter_decode_children(self, elem, validation='lax', index=0):
        """
        Creates an iterator for decoding the children of an element. Before ending the generator
        yields the last index used by inner validators.

        :param elem: The parent Element.
        :param index: Start child index, 0 for default.
        :param validation: Validation mode that can be 'strict', 'lax' or 'skip'.
        :return: Yields a sequence of values that can be tuples and/or errors and an integer at the end.
        """
        model_occurs = 0
        while True:
            try:
                child = elem[index]
            except IndexError:
                if validation != 'skip' and model_occurs == 0 and self.min_occurs > 0:
                    yield self.children_validation_error(validation, elem, index, expected=[self])
                yield index
                return
            else:
                tag = child.tag
                if callable(tag):
                    # When tag is a function the child is a <class 'lxml.etree._Comment'>
                    index += 1
                    continue
                elif tag == self.name:
                    yield self, child
                elif not self.qualified and tag == get_qname(self.target_namespace, self.name):
                    yield self, child
                elif self.name in self.maps.substitution_groups:
                    for e in self.schema.substitution_groups[self.name]:
                        if tag == e.name:
                            yield e, child
                            break
                    else:
                        if validation != 'skip' and model_occurs == 0 and self.min_occurs > 0:
                            yield self.children_validation_error(validation, elem, index, expected=[self])
                        yield index
                        return

                else:
                    if validation != 'skip' and model_occurs == 0 and self.min_occurs > 0:
                        yield self.children_validation_error(validation, elem, index, expected=[self])
                    yield index
                    return

            index += 1
            model_occurs += 1
            if self.max_occurs is not None and model_occurs >= self.max_occurs:
                yield index
                return

    def iter_encode(self, obj, validation='lax', converter=None, **kwargs):
        """
        Creates an iterator for encoding data to an Element.

        :param obj: the data that has to be encoded.
        :param validation: the validation mode: can be 'lax', 'strict' or 'skip'.
        :param converter: an :class:`XMLSchemaConverter` subclass or instance.
        :param kwargs: keyword arguments for the encoding process.
        :return: yields an Element, eventually preceded by a sequence of \
        validation or encoding errors.
        """
        if not isinstance(converter, XMLSchemaConverter):
            converter = self.schema.get_converter(converter, **kwargs)
        level = kwargs.pop('level', 0)
        element_data = converter.element_encode(obj, self, level)

        try:
            type_name = element_data.attributes[XSI_TYPE]
        except (KeyError, AttributeError):
            type_ = self.type
        else:
            type_ = self.maps.lookup_type(converter.unmap_qname(type_name))

        if type_.is_complex():
            for result in type_.iter_encode(element_data, validation, converter, level=level + 1, **kwargs):
                if isinstance(result, XMLSchemaValidationError):
                    yield self.update_error(result, obj, **kwargs)
                else:
                    yield converter.etree_element(element_data.tag, *result, level=level)
                    break
            else:
                yield converter.etree_element(element_data.tag, level=level)
        else:
            for result in self.attributes.iter_encode(element_data.attributes, validation, **kwargs):
                if isinstance(result, XMLSchemaValidationError):
                    yield result
                else:
                    attributes = result
                    break
            else:
                attributes = None

            if element_data.content:
                reason = "a simpleType element can't has child elements."
                yield self.validation_error(validation, reason, obj, **kwargs)

            if element_data.text is None:
                yield converter.etree_element(element_data.tag, attrib=attributes, level=level)
            else:
                for result in type_.iter_encode(element_data.text, validation, **kwargs):
                    if isinstance(result, XMLSchemaValidationError):
                        yield self.update_error(result, obj, **kwargs)
                    else:
                        yield converter.etree_element(element_data.tag, result, attrib=attributes, level=level)
                        break
                else:
                    yield converter.etree_element(element_data.tag, attrib=attributes, level=level)

        del element_data

    def is_restriction(self, other, check_particle=True):
        if isinstance(other, XsdAnyElement):
            return True  # TODO
        elif isinstance(other, XsdElement):
            if self.name != other.name:
                if other.name not in self.maps.substitution_groups:
                    return False
                else:
                    return any(self.is_restriction(e) for e in self.maps.substitution_groups[other.name])
            elif check_particle and not ParticleMixin.is_restriction(self, other):
                return False
            elif self.type is not other.type and self.type.elem is not other.type.elem and \
                    not self.type.is_derived(other.type):
                return False
            elif self.fixed != other.fixed:
                return False
            elif other.nillable is False and self.nillable:
                return False
            elif not all(value in other.block for value in self.block):
                return False
            elif not all(k in other.constraints for k in self.constraints):
                return False
        elif other.model == 'choice':
            if ParticleMixin.is_restriction(self, other):
                return any(self.is_restriction(e, False) for e in other.iter_group())
            else:
                return any(self.is_restriction(e) for e in other.iter_group())
        else:
            match_restriction = False
            for e in other.iter_group():
                if match_restriction:
                    if not e.is_emptiable():
                        return False
                elif self.is_restriction(e):
                    match_restriction = True
                elif not e.is_emptiable():
                    return False
        return True


class Xsd11Element(XsdElement):
    """
    Class for XSD 1.1 'element' declarations.

    <element
      abstract = boolean : false
      block = (#all | List of (extension | restriction | substitution))
      default = string
      final = (#all | List of (extension | restriction))
      fixed = string
      form = (qualified | unqualified)
      id = ID
      maxOccurs = (nonNegativeInteger | unbounded)  : 1
      minOccurs = nonNegativeInteger : 1
      name = NCName
      nillable = boolean : false
      ref = QName
      substitutionGroup = List of QName
      targetNamespace = anyURI
      type = QName
      {any attributes with non-schema namespace . . .}>
      Content: (annotation?, ((simpleType | complexType)?, alternative*, (unique | key | keyref)*))
    </element>
    """
    def _parse(self):
        XsdComponent._parse(self)
        self._parse_attributes()
        index = self._parse_type()
        index = self._parse_alternatives(index)
        if self.type is None:
            if not self.alternatives:
                self.type = self.maps.lookup_type(XSD_ANY_TYPE)
        elif self.alternatives:
            self.parse_error("types alternatives incompatible with type specification.")

        self._parse_constraints(index)
        self._parse_substitution_group()

    def _parse_alternatives(self, index=0):
        self.alternatives = []
        for child in self._iterparse_components(self.elem, start=index):
            if child.tag == XSD_ALTERNATIVE_TAG:
                self.alternatives.append(XsdAlternative(child, self.schema, self))
                index += 1
            else:
                break
        return index

    @property
    def target_namespace(self):
        try:
            return self.elem.attrib['targetNamespace']
        except KeyError:
            return self.schema.target_namespace


class XsdAlternative(XsdComponent):
    """
    <alternative
      id = ID
      test = an XPath expression
      type = QName
      xpathDefaultNamespace = (anyURI | (##defaultNamespace | ##targetNamespace | ##local))
      {any attributes with non-schema namespace . . .}>
      Content: (annotation?, (simpleType | complexType)?)
    </alternative>
    """
    admitted_tags = {XSD_ALTERNATIVE_TAG}

    @property
    def built(self):
        raise NotImplementedError
