"""Testes do lado de casa (assinaturas), sem rede:  python teste_puxar.py"""
import base64
import hashlib
import hmac
import time

import puxar

CORPO = b'{"type":"payment.succeeded","data":{"id":"pay_xxxxxxx"}}'
SEG_STD = 'whsec_' + base64.b64encode(b'segredo-de-teste-123456').decode()
SEG_CRU = 'segredo_cru_sem_base64!'


def assinar_com(k, wid, ts, corpo=CORPO):
    return 'v1,' + base64.b64encode(hmac.new(k, f'{wid}.{ts}.'.encode() + corpo, hashlib.sha256).digest()).decode()


def main():
    ts = str(int(time.time()))
    k_std = base64.b64decode(SEG_STD.split('_', 1)[1])
    assert puxar.verificar(CORPO, 'msg_1', ts, assinar_com(k_std, 'msg_1', ts), SEG_STD) == k_std
    assert puxar.verificar(CORPO, 'msg_1', ts, assinar_com(SEG_CRU.encode(), 'msg_1', ts), SEG_CRU) == SEG_CRU.encode()
    assert puxar.verificar(CORPO + b' ', 'msg_1', ts, assinar_com(k_std, 'msg_1', ts), SEG_STD) is None
    assert puxar.verificar(CORPO, 'msg_2', ts, assinar_com(k_std, 'msg_1', ts), SEG_STD) is None
    assert puxar.verificar(CORPO, 'msg_1', ts, 'v1,AAAA', SEG_STD) is None
    assert puxar.verificar(CORPO, 'msg_1', ts, assinar_com(k_std, 'msg_1', ts), '') is None
    # reassinar com a chave que validou: o destino aceita com o mesmo segredo e o mesmo id
    cab = puxar.assinar(CORPO, 'msg_1', k_std)
    assert cab['webhook-id'] == 'msg_1'
    assert puxar.verificar(CORPO, 'msg_1', cab['webhook-timestamp'], cab['webhook-signature'], SEG_STD) == k_std
    print('6 + 2 verificacoes OK')


if __name__ == '__main__':
    main()
