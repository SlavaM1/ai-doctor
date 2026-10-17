import { Icon } from './components'

export default function AlertsPage() {
  return <section className="settings-page">
    <div className="development-note" role="status"><strong>Страница пока находится в разработке</strong><p>В будущем будет доступна отправка уведомлений об ошибках в Telegram. Настройки ниже — пример: они не сохраняются, уведомления не отправляются. Не вводите настоящий токен бота.</p></div>
    <div className="settings-section"><div className="settings-intro"><Icon name="alerts" /><h2>Уведомления в Telegram</h2><p>Сообщения о новых инцидентах и результатах диагностики выбранного проекта. Ошибки из исключений не будут отправляться.</p></div>
      <fieldset className="settings-fields alert-fields" disabled><legend className="sr-only">Пример будущих настроек уведомлений</legend>
        <label className="acknowledgement"><input type="checkbox" />Отправлять уведомления</label>
        <label>Токен бота<input type="password" placeholder="Токен от BotFather" autoComplete="off" /></label>
        <label>Идентификатор чата<input placeholder="Например, -1001234567890" /></label>
        <label>Важность ошибок<select defaultValue="errors"><option value="errors">Ошибки и критические сбои</option><option value="critical">Только критические сбои</option><option value="all">Включая предупреждения</option></select></label>
        <label>Повторное уведомление<select defaultValue="15"><option value="15">Не чаще раза в 15 минут</option><option value="60">Не чаще раза в час</option></select></label>
        <label className="acknowledgement"><input type="checkbox" defaultChecked />Сообщать о завершении диагностики</label>
      </fieldset>
    </div><div className="settings-save"><p>Сохранение станет доступно после подключения уведомлений.</p><button className="button primary" disabled>Сохранить настройки</button></div>
  </section>
}
