def scope(format, clock, expires=None):
    return dict(format=format, kind='protected_message' if format == 'LME.binary' else 'knowledge',
                application_expires_epoch_us=expires or clock()[0] + 60000000)
