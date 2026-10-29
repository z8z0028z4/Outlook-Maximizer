import unittest

from outlook_maximizer.demo import demo_data


class DemoLanguageTests(unittest.TestCase):
    def test_demo_has_sample_content_for_both_languages(self):
        english_mail, english_analysis = demo_data("en")
        chinese_mail, chinese_analysis = demo_data("zh-TW")
        self.assertIn("Project", english_mail.subject)
        self.assertIn("專案", chinese_mail.subject)
        for mail, analysis in ((english_mail, english_analysis), (chinese_mail, chinese_analysis)):
            with self.subTest(subject=mail.subject):
                self.assertTrue(analysis.summary)
                self.assertTrue(analysis.reply_draft)
                self.assertEqual(len(analysis.events), 1)
                analysis.events[0].validate(for_insert=True)


if __name__ == "__main__":
    unittest.main()
