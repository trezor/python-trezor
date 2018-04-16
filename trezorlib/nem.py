import binascii
import json

from trezorlib import mapping
from . import messages as proto

TYPE_TRANSACTION_TRANSFER = 0x0101
TYPE_IMPORTANCE_TRANSFER = 0x0801
TYPE_AGGREGATE_MODIFICATION = 0x1001
TYPE_MULTISIG_SIGNATURE = 0x1002
TYPE_MULTISIG = 0x1004
TYPE_PROVISION_NAMESPACE = 0x2001
TYPE_MOSAIC_CREATION = 0x4001
TYPE_MOSAIC_SUPPLY_CHANGE = 0x4002


adapter_nem_transaction_common = mapping.ProtoAdapter(
    proto.NEMTransactionCommon,
    {
        'network': lambda t: (t['version'] >> 24) & 0xff,
        'timestamp': 'timeStamp',
    },
)


adapter_nem_transfer = mapping.ProtoAdapter(
    proto.NEMTransfer,
    {
        'payload': 'message.payload',
        'public_key': 'message.publicKey',
        'mosaics': [{
            'namespace': 'mosaicId.namespaceId',
            'mosaic': 'mosaicId.name'
        }],
    },
)


adapter_nem_aggregate_modification = mapping.ProtoAdapter(
    proto.NEMAggregateModification,
    {
        'modifications': [{
            'type': 'modificationType',
            'public_key': 'cosignatoryAccount',
        }],
        'relative_change': 'minCosignatories.relativeChange',
    }
)


adapter_nem_provision_namespace = mapping.ProtoAdapter(
    proto.NEMProvisionNamespace,
    {
        'namespace': 'newPart',
        'sink': 'rentalFeeSink',
        'fee': 'rentalFee',
    },
)


adapter_modified_mosaic_definition = mapping.ProtoAdapter(
    proto.NEMMosaicDefinition,
    {
        'namespace': 'id.namespaceId',
        'mosaic': 'id.name',
        'levy': 'levy.type',
        'fee': 'levy.fee',
        'levy_address': 'levy.recipient',
        'levy_namespace': 'levy.mosaicId.namespaceId',
        'levy_mosaic': 'levy.mosaicId.name',
        'divisibility': 'properties.divisibility',
        'supply': 'properties.initialSupply',
        'mutable_supply': 'properties.supplyMutable',
        'transferable': 'properties.transferable',
    },
)


def adapter_nem_mosaic_definition(transaction):
    definition = transaction['mosaicDefinition'].copy()
    properties = definition['properties']
    definition['properties'] = {prop['name']: json.loads(prop['value']) for prop in properties}
    return adapter_modified_mosaic_definition(definition)


adapter_nem_mosaic_creation = mapping.ProtoAdapter(
    proto.NEMMosaicCreation,
    {
        'sink': 'creationFeeSink',
        'fee': 'creationFee',
        'definition': adapter_nem_mosaic_definition,
    }
)


adapter_nem_supply_change = mapping.ProtoAdapter(
    proto.NEMMosaicSupplyChange,
    {
        'namespace': 'mosaicId.namespaceId',
        'mosaic': 'mosaicId.name',
        'type': 'supplyType'
    },
)


adapter_nem_importance_transfer = mapping.ProtoAdapter(
    proto.NEMImportanceTransfer,
    select_field='importanceTransfer',
)


def create_sign_tx(transaction):
    msg = proto.NEMSignTx()
    msg.transaction = adapter_nem_transaction_common(transaction)
    msg.cosigning = (transaction["type"] == TYPE_MULTISIG_SIGNATURE)

    if transaction["type"] in (TYPE_MULTISIG_SIGNATURE, TYPE_MULTISIG):
        transaction = transaction["otherTrans"]
        msg.multisig = adapter_nem_transaction_common(transaction)
    elif "otherTrans" in transaction:
        raise ValueError("Transaction does not support inner transaction")

    tx_type = transaction["type"]
    if tx_type == TYPE_TRANSACTION_TRANSFER:
        msg.transfer = adapter_nem_transfer(transaction)
    elif tx_type == TYPE_AGGREGATE_MODIFICATION:
        msg.aggregate_modification = adapter_nem_aggregate_modification(transaction)
    elif tx_type == TYPE_PROVISION_NAMESPACE:
        msg.provision_namespace = adapter_nem_provision_namespace(transaction)
    elif tx_type == TYPE_MOSAIC_CREATION:
        msg.mosaic_creation = adapter_nem_mosaic_creation(transaction)
    elif tx_type == TYPE_MOSAIC_SUPPLY_CHANGE:
        msg.supply_change = adapter_nem_supply_change(transaction)
    elif tx_type == TYPE_IMPORTANCE_TRANSFER:
        msg.importance_transfer = adapter_nem_importance_transfer(transaction)
    else:
        raise ValueError("Unknown transaction type")

    return msg
