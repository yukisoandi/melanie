from __future__ import annotations

import msgpack
import msgspec
import orjson
from pydantic import (
    UUID1,
    UUID3,
    UUID4,
    UUID5,
    VERSION,
    AmqpDsn,
    AnyHttpUrl,
    AnyUrl,
    BaseSettings,
    ByteSize,
    CockroachDsn,
    ConfigDict,
    ConstrainedBytes,
    ConstrainedDate,
    ConstrainedDecimal,
    ConstrainedFloat,
    ConstrainedFrozenSet,
    ConstrainedInt,
    ConstrainedList,
    ConstrainedSet,
    ConstrainedStr,
    DirectoryPath,
    EmailStr,
    Extra,
    Field,
    FilePath,
    FileUrl,
    FiniteFloat,
    FutureDate,
    HttpUrl,
    IPvAnyAddress,
    IPvAnyInterface,
    IPvAnyNetwork,
    Json,
    JsonWrapper,
    KafkaDsn,
    MongoDsn,
    NameEmail,
    NegativeFloat,
    NegativeInt,
    NoneBytes,
    NoneStr,
    NoneStrBytes,
    NonNegativeFloat,
    NonNegativeInt,
    NonPositiveFloat,
    NonPositiveInt,
    PastDate,
    PaymentCardNumber,
    PositiveFloat,
    PositiveInt,
    PostgresDsn,
    PrivateAttr,
    Protocol,
    PyObject,
    RedisDsn,
    Required,
    SecretBytes,
    SecretField,
    SecretStr,
    StrBytes,
    StrictBool,
    StrictBytes,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
    compiled,
    conbytes,
    condate,
    condecimal,
    confloat,
    confrozenset,
    conint,
    conlist,
    conset,
    constr,
    create_model,
    create_model_from_namedtuple,
    create_model_from_typeddict,
    parse_file_as,
    parse_obj_as,
    parse_raw_as,
    root_validator,
    schema_json_of,
    schema_of,
    stricturl,
    validate_arguments,
    validate_email,
    validate_model,
    validator,
)
from pydantic import BaseConfig as _BaseConfig
from pydantic import BaseModel as _BaseModel

UUID1 = UUID1
UUID3 = UUID3
UUID4 = UUID4
UUID5 = UUID5
VERSION = VERSION
AmqpDsn = AmqpDsn
AnyHttpUrl = AnyHttpUrl
AnyUrl = AnyUrl
BaseSettings = BaseSettings
ByteSize = ByteSize
CockroachDsn = CockroachDsn
ConfigDict = ConfigDict
ConstrainedBytes = ConstrainedBytes
ConstrainedDate = ConstrainedDate
ConstrainedDecimal = ConstrainedDecimal
ConstrainedFloat = ConstrainedFloat
ConstrainedFrozenSet = ConstrainedFrozenSet
ConstrainedInt = ConstrainedInt
ConstrainedList = ConstrainedList
ConstrainedSet = ConstrainedSet
ConstrainedStr = ConstrainedStr
DirectoryPath = DirectoryPath
EmailStr = EmailStr
Extra = Extra
Field = Field
FilePath = FilePath
FileUrl = FileUrl
FiniteFloat = FiniteFloat
FutureDate = FutureDate
HttpUrl = HttpUrl
IPvAnyAddress = IPvAnyAddress
IPvAnyInterface = IPvAnyInterface
IPvAnyNetwork = IPvAnyNetwork
Json = Json
JsonWrapper = JsonWrapper
KafkaDsn = KafkaDsn
MongoDsn = MongoDsn
NameEmail = NameEmail
NegativeFloat = NegativeFloat
NegativeInt = NegativeInt
NoneBytes = NoneBytes
NoneStr = NoneStr
NoneStrBytes = NoneStrBytes
NonNegativeFloat = NonNegativeFloat
NonNegativeInt = NonNegativeInt
NonPositiveFloat = NonPositiveFloat
NonPositiveInt = NonPositiveInt
PastDate = PastDate
PaymentCardNumber = PaymentCardNumber
PositiveFloat = PositiveFloat
PositiveInt = PositiveInt
PostgresDsn = PostgresDsn
PrivateAttr = PrivateAttr
Protocol = Protocol
PyObject = PyObject
RedisDsn = RedisDsn
Required = Required
SecretBytes = SecretBytes
SecretField = SecretField
SecretStr = SecretStr
StrBytes = StrBytes
StrictBool = StrictBool
StrictBytes = StrictBytes
StrictFloat = StrictFloat
StrictInt = StrictInt
StrictStr = StrictStr
ValidationError = ValidationError
compiled = compiled
conbytes = conbytes
condate = condate
condecimal = condecimal
confloat = confloat
confrozenset = confrozenset
conint = conint
conlist = conlist
conset = conset
constr = constr
create_model = create_model
create_model_from_namedtuple = create_model_from_namedtuple
create_model_from_typeddict = create_model_from_typeddict
parse_file_as = parse_file_as
parse_obj_as = parse_obj_as
parse_raw_as = parse_raw_as
root_validator = root_validator
schema_json_of = schema_json_of
schema_of = schema_of
stricturl = stricturl
validate_arguments = validate_arguments
validate_email = validate_email
validate_model = validate_model
validator = validator


def custom_hook(obj):
    if hasattr(obj, "__str__"):
        return str(obj)
    if isinstance(obj, str):
        return str(obj)

    return orjson.dumps(obj, option=orjson.OPT_NON_STR_KEYS | orjson.OPT_SERIALIZE_NUMPY).decode()


def orjson_dumps2(v) -> str:
    return orjson.dumps(v).decode()


def msgspec_dumps(v, *, default) -> str:
    return msgspec.json.encode(v, enc_hook=custom_hook).decode("UTF-8")


def msgspec_loads(v, *a):
    return msgspec.json.decode(v, strict=False)


def orjson_dumps(v, *, default) -> str:
    return orjson.dumps(v, default=default, option=orjson.OPT_NON_STR_KEYS).decode("UTF-8")


class BaseModel(_BaseModel):
    class Config(_BaseConfig):
        allow_population_by_field_name = True
        arbitrary_types_allowed = True
        json_dumps = orjson_dumps
        use_enum_values = True
        json_loads = orjson.loads
        orm_mode = True
        smart_union = True
        copy_on_model_validation = "deep"

    @classmethod
    def valid_load(cls, obj) -> BaseModel:
        from melanie.core import snake_cased_dict

        obj = snake_cased_dict(obj)
        return cls.parse_obj(obj)

    def to_bytes(self, *a, **ka) -> bytes:
        return msgpack.packb(self.dict())

    @classmethod
    def from_bytes(cls, payload: bytes) -> BaseModel:
        return cls.parse_obj(msgpack.unpackb(payload))

    def jsonb(self, *a, **ka) -> bytes:
        return self.json().encode("UTF-8")
