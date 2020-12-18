LANGS=(nl)

pybabel extract -F babel.cfg -o hanabi.pot -c "i18n" .
for lang in $LANGS 
do
    pybabel update -i hanabi.pot -d translations -l $lang -D hanabi
done
